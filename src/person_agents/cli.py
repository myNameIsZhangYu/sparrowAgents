import asyncio
import json
import signal
import sys
from pathlib import Path

import structlog
import typer
from rich.console import Console

from person_agents import __version__
from person_agents.config.settings import settings
from person_agents.llm.fallback import LLMFallbackError
from person_agents.observability.cost import CostTracker
from person_agents.observability.logging import setup_logging

app = typer.Typer(name="person-agents", help="Personal agent CLI")
console = Console()
logger = structlog.get_logger()


def _signal_handler(sig: int, frame: object) -> None:
    # 记录优雅退出信号后直接结束 CLI 进程。
    logger.info("graceful_shutdown", signal=sig)
    sys.exit(0)


@app.command()
def version() -> None:
    """Show version."""
    console.print(f"person-agents v{__version__}")


@app.command()
def doctor(
    production: bool = typer.Option(False, "--production", help="Validate production deployment requirements."),
) -> None:
    """Check system health: config, LLM API, Ollama, directories."""
    setup_logging()
    checks: list[tuple[str, bool, str]] = []

    # doctor 检查本地资源、静态配置和外部依赖健康。
    checks.append(("OpenAI API key", bool(settings.openai_api_key), "not set" if not settings.openai_api_key else "ok"))
    checks.append(("Data directory", settings.data_dir.exists(), str(settings.data_dir)))
    checks.append(("SQLite path writable", _check_write(settings.sqlite_path.parent), str(settings.sqlite_path)))

    try:
        import httpx
        # Ollama 探测只检查 /api/tags，不发起昂贵推理。
        resp = httpx.get(settings.ollama_base_url + "/api/tags", timeout=3)
        checks.append(("Ollama", resp.status_code == 200, settings.ollama_base_url))
    except httpx.HTTPError:
        checks.append(("Ollama", False, "unreachable"))

    for name, ok, detail in checks:
        status = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {status} {name}: {detail}")

    if production:
        from person_agents.config.validation import ConfigurationError, validate_production_settings

        # production 模式启用严格配置门，失败直接非零退出。
        try:
            validate_production_settings(settings)
            console.print("  [green]✓[/green] Production configuration: ok")
        except ConfigurationError as exc:
            console.print(f"  [red]✗[/red] Production configuration: {exc}")
            raise typer.Exit(code=2) from exc


def _check_write(path) -> bool:
    try:
        # 创建并删除探针文件，验证真实文件系统写权限。
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".write_test"
        test_file.touch()
        test_file.unlink()
        return True
    except OSError:
        return False


@app.command()
def eval(
    output: str = typer.Option("", help="Write the JSON evaluation report to this path."),
    threshold: float = typer.Option(0.0, help="Fail when pass rate is below this threshold."),
) -> None:
    """Run the baseline LLM evaluation suite and print a JSON report."""
    setup_logging()
    from person_agents.eval.cases.baseline import BASELINE_CASES
    from person_agents.eval.runner import EvalRunner

    async def _eval():
        from person_agents.eval.storage import EvalStorage

        # eval 报告输出后可选保存；设置 threshold 时作为质量门。
        runner = EvalRunner()
        report = await runner.run_all(BASELINE_CASES)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        console.print(rendered)
        if output:
            Path(output).write_text(rendered + "\n", encoding="utf-8")
        if threshold > 0:
            storage = EvalStorage()
            storage.save_result(report, report.get("model", "unknown"))
            if not storage.compare(threshold):
                console.print(f"[red]Evaluation gate failed:[/red] {report['pass_rate']:.2f} < {threshold:.2f}")
                raise typer.Exit(code=2)

    asyncio.run(_eval())


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8000, help="Bind port"),
) -> None:
    """Run the authenticated HTTP API."""
    import uvicorn

    uvicorn.run("person_agents.api.app:app", host=host, port=port)


@app.command()
def worker() -> None:
    """Run the background agent worker."""
    setup_logging()
    from person_agents.graph.runtime import GraphServices
    from person_agents.runtime.job_store import create_job_store
    from person_agents.runtime.worker import worker_loop

    asyncio.run(worker_loop(create_job_store(), services=GraphServices.default()))


@app.command()
def migrate() -> None:
    """Bootstrap PostgreSQL runtime schemas."""
    from person_agents.runtime.startup import wait_for_migrations

    # 迁移入口独立于 serve，保证部署顺序可控。
    asyncio.run(wait_for_migrations())


@app.command()
def run(
    prompt: str = typer.Argument(..., help="User prompt"),
    system: str = typer.Option("You are a helpful personal assistant.", help="System prompt"),
) -> None:
    """Send a prompt through the LLM fallback chain."""
    setup_logging()
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # 当前 CLI tracker 只用于会话展示，不参与服务端配额。
    CostTracker(session_id="cli-session")

    async def _run():
        final_output = await _run_agent_prompt(prompt, "cli-run")
        if final_output:
            console.print(final_output)
        else:
            console.print("[dim]Task completed.[/dim]")

    asyncio.run(_run())


async def _run_agent_prompt(prompt: str, session_id: str) -> str:
    from person_agents.graph.engine import run_graph
    from person_agents.graph.runtime import GraphServices
    from person_agents.security.context import RequestContext

    # CLI 使用默认服务组合；run_graph 内部仍处理 fast reply 与循环守卫。
    result = await run_graph(
        prompt,
        session_id=session_id,
        context=RequestContext(user_id=session_id, tenant_id="default", session_id=session_id),
        services=GraphServices.default(),
    )
    return result.get("tool_results", [{}])[-1].get("output", "") if result.get("tool_results") else ""


@app.command()
def chat() -> None:
    """Interactive chat loop."""
    setup_logging()
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    console.print("[bold]person-agents chat[/bold] (Ctrl+C to exit)")
    tracker = CostTracker(session_id="chat-session")

    async def _chat() -> None:
        while True:
            try:
                # Rich input 是阻塞 API，放入线程避免卡住事件循环。
                user_input = await asyncio.to_thread(console.input, "[bold blue]> [/bold blue]")
                if not user_input.strip():
                    continue
                final_output = await _run_agent_prompt(user_input, "cli-chat")
                if final_output:
                    console.print(final_output)
            except KeyboardInterrupt:
                console.print(f"\n[dim]session: {tracker.total_calls} calls, {tracker.total_tokens} tokens[/dim]")
                break
            except LLMFallbackError as exc:
                console.print(f"[red]Error:[/red] {exc}")

    asyncio.run(_chat())


if __name__ == "__main__":
    app()
