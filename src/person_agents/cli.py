import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import structlog
import typer
from rich.console import Console

from person_agents import __version__
from person_agents.config.settings import settings
from person_agents.graph.render import render_agent_result
from person_agents.llm.fallback import LLMFallbackError, llm_is_available
from person_agents.observability.cost import CostTracker
from person_agents.observability.langfuse import shutdown_langfuse
from person_agents.observability.logging import setup_logging
from person_agents.security.context import RequestContext

app = typer.Typer(name="person-agents", help="Personal agent CLI")
console = Console()
logger = structlog.get_logger()
_cli_chat_session = f"cli-chat-{os.getpid()}"
_cli_state = {"pending_confirmation": None, "workspace": None}


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


def _shutdown_runtime_threads() -> None:
    shutdown_langfuse()


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
    shutdown_langfuse()


@app.command("eval-multi-agent")
def eval_multi_agent() -> None:
    """Run the multi-agent task graph evaluation suite."""
    setup_logging()

    async def _eval():
        from person_agents.eval.multi_agent_runner import run_multi_agent_eval

        report = await run_multi_agent_eval()
        console.print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["passed"]:
            raise typer.Exit(code=2)

    asyncio.run(_eval())
    shutdown_langfuse()


@app.command("eval-cli-workflow")
def eval_cli_workflow() -> None:
    """Run the real create-file, confirm, and verify workflow."""
    import tempfile

    setup_logging()

    async def _eval():
        from person_agents.eval.cli_cases import run_cli_workflow_eval

        with tempfile.TemporaryDirectory() as workspace:
            report = await run_cli_workflow_eval(Path(workspace))
        console.print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["passed"]:
            raise typer.Exit(code=2)

    asyncio.run(_eval())
    shutdown_langfuse()


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
    workspace: str = typer.Option("", help="Workspace directory for file tools; empty means current directory."),
) -> None:
    """Send a prompt through the LLM fallback chain."""
    setup_logging()
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # 先做轻量可用性探测；避免无模型时启动图运行时和计时器。
    if not asyncio.run(llm_is_available()):
        console.print("No LLM provider is available")
        raise typer.Exit(code=1)

    # 当前 CLI tracker 只用于会话展示，不参与服务端配额。
    CostTracker(session_id="cli-session")

    async def _run():
        try:
            final_output = await _run_agent_prompt(prompt, "cli-run", workspace=workspace)
        except LLMFallbackError as exc:
            console.print(f"[red]Agent execution failed: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        if final_output:
            console.print(final_output)
        else:
            console.print("[dim]Task completed.[/dim]")

    try:
        asyncio.run(_run())
    except TimeoutError:
        console.print("[red]Agent execution timed out.[/red]")
    finally:
        _shutdown_runtime_threads()
    raise typer.Exit()


async def _run_agent_prompt(prompt: str, session_id: str, workspace: str = "") -> str:
    from person_agents.graph import engine
    from person_agents.graph.runtime import GraphServices

    run_graph = engine.run_graph

    # CLI 使用默认服务组合；run_graph 内部仍处理 fast reply 与循环守卫。
    result = await run_graph(
        prompt,
        session_id=session_id,
        context=RequestContext(user_id=session_id, tenant_id="default", session_id=session_id),
        services=GraphServices.default(workspace_root=workspace or None),
    )
    if result.get("status") == "confirmation_required":
        _cli_state["pending_confirmation"] = result.get("pending_confirmation")
        _cli_state["workspace"] = workspace or str(Path.cwd())
    return render_agent_result(result)


async def _complete_pending_tool(confirmation_token: str, session_id: str) -> str:
    from person_agents.graph.runtime import GraphServices
    from person_agents.graph.tool_confirmation import confirm_pending_tool

    services = GraphServices.default(workspace_root=_cli_state.get("workspace"))
    result = await confirm_pending_tool(
        confirmation_token,
        context=RequestContext(user_id=session_id, tenant_id="default", session_id=session_id),
        registry=services.registry,
        services=services,
        resume=True,
        session_id=session_id,
    )
    if isinstance(result, dict) and result.get("status") == "confirmation_required":
        return f"仍需确认：/confirm {result.get('confirmation_token')}"
    if not isinstance(result, dict):
        return str(result)
    return render_agent_result(result)


@app.command()
def chat(
    workspace: str = typer.Option("", help="Workspace directory for file tools; empty means current directory."),
) -> None:
    """Interactive chat loop."""
    setup_logging()
    # uv 自带的 libedit readline 对多字节字符删除处理不完整；
    # chat 使用系统终端输入，Backspace 可直接删除一个 Unicode 字符。
    os.environ.pop("PYTHONSTARTUP", None)
    import readline

    readline.parse_and_bind("bind ^? backward-delete-char")
    readline.parse_and_bind("bind ^H backward-delete-char")
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
                if user_input.startswith("/confirm"):
                    token = user_input.removeprefix("/confirm").strip() or (_cli_state["pending_confirmation"] or "")
                    if not token:
                        console.print("[red]当前没有待确认操作。[/red]")
                        continue
                    try:
                        console.print(await _complete_pending_tool(token, _cli_chat_session))
                        if token == _cli_state["pending_confirmation"]:
                            _cli_state["pending_confirmation"] = None
                    except PermissionError as exc:
                        console.print(f"[red]确认失败：{exc}[/red]")
                        if _cli_state["pending_confirmation"]:
                            console.print("可执行：")
                            console.print(f"/confirm {_cli_state['pending_confirmation']}")
                    continue
                final_output = await _run_agent_prompt(user_input, _cli_chat_session, workspace=workspace)
                if final_output:
                    console.print(final_output)
            except KeyboardInterrupt:
                console.print(f"\n[dim]session: {tracker.total_calls} calls, {tracker.total_tokens} tokens[/dim]")
                break
            except LLMFallbackError as exc:
                console.print(f"[red]Error:[/red] {exc}")

    asyncio.run(_chat())
    shutdown_langfuse()


if __name__ == "__main__":
    app()
