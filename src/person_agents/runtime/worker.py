import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from prometheus_client import Counter

from person_agents.config.settings import settings
from person_agents.governance.rate_limiter import RateLimiter
from person_agents.memory.postgres_store import PostgresStore
from person_agents.runtime.job_store import create_job_store
from person_agents.runtime.jobs import JobManager
from person_agents.runtime.state import create_state_store
from person_agents.security.context import RequestContext

logger = structlog.get_logger()

run_attempts_total = Counter("run_attempts_total", "Run execution attempts", ["status"])
run_dead_letter_total = Counter("run_dead_letter_total", "Runs moved to dead letter")


async def run_one(job: dict, store, worker_id: str = "inline", lease_seconds: int = 60, services: Any | None = None) -> None:
    from person_agents.graph.engine import run_graph
    # 状态存储负责取消信号；rate limiter 负责异步任务最终 token / 成本统计。
    state_store = create_state_store(settings.redis_url)
    rate_limiter = RateLimiter(state_store)

    postgres = isinstance(store, PostgresStore)
    if not postgres:
        # SQLite 路径通过 JobManager 包装，保证两个后端行为一致。
        manager = JobManager(store)

    async def poll_cancellation() -> None:
        while True:
            # Redis 取消标记是共享状态，多 worker 部署下也能立即生效。
            if state_store.get_json(f"cancel:{job['run_id']}"):
                raise TimeoutError("Run canceled")
            await asyncio.sleep(0.2)

    cancellation_task = asyncio.create_task(poll_cancellation())
    if postgres:
        # 开工前续租，防止任务在队列中等待过久后已被其他 worker 认领。
        await store.renew_run_lease(job["run_id"], worker_id, lease_seconds)
    else:
        manager.renew_lease(job["run_id"], worker_id, lease_seconds)
    context = RequestContext(
        user_id=job["owner_user_id"],
        tenant_id=job["tenant_id"],
        session_id=job["session_id"],
    )
    try:
        graph_task = asyncio.create_task(
            run_graph(job["prompt"], session_id=job["session_id"], context=context, services=services)
        )
        done, _ = await asyncio.wait(
            {graph_task, cancellation_task},
            timeout=settings.run_timeout_s,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancellation_task in done:
            # 用户取消优先于正常完成；必须取消图任务再更新状态。
            graph_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await graph_task
            raise TimeoutError("Run canceled")
        if graph_task not in done:
            # 外层超时先取消任务，避免事件循环中留下悬挂协程。
            graph_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await graph_task
            raise TimeoutError("Run timed out")
        cancellation_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancellation_task
        result = graph_task.result()
        llm_result = result.get("llm_response")
        # 异步任务只在有汇总响应时补记 token / cost。
        tokens, cost = rate_limiter.record_llm_usage(llm_result, job["owner_user_id"], job["tenant_id"]) if llm_result else (0, 0.0)
    except (RuntimeError, ValueError, TimeoutError, OSError) as exc:
        attempts = int(job.get("attempts", 1))
        # 读取最新状态，防止“先取消后异常”的竞态被错误重试。
        current_run = await store.get_run(job["run_id"], job["owner_user_id"], job["tenant_id"]) if postgres else manager.get(job["run_id"], job["owner_user_id"], job["tenant_id"])
        if current_run and current_run.get("status") == "canceled":
            return
        if str(exc) == "Run canceled":
            if postgres:
                await store.update_run(job["run_id"], job["owner_user_id"], job["tenant_id"], "canceled", error=str(exc))
            else:
                manager.update(job["run_id"], job["owner_user_id"], job["tenant_id"], "canceled", error=str(exc))
            return
        if attempts >= settings.run_max_attempts:
            # 达到最大尝试次数后进入 dead letter，等待人工或运维处理。
            if postgres:
                await store.update_run(job["run_id"], job["owner_user_id"], job["tenant_id"], "dead_letter", error=str(exc))
            else:
                manager.update(job["run_id"], job["owner_user_id"], job["tenant_id"], "dead_letter", error=str(exc))
            run_attempts_total.labels("dead_letter").inc()
            run_dead_letter_total.inc()
            return
        retry_at = datetime.now(UTC) + timedelta(seconds=settings.run_retry_backoff_seconds * (2 ** (attempts - 1)))
        # 指数退避可以避免同一失败任务持续冲击模型和存储。
        if postgres:
            await store.update_run(job["run_id"], job["owner_user_id"], job["tenant_id"], "queued", error=str(exc))
            await store.defer_run_retry(job["run_id"], retry_at)
        else:
            manager.update(job["run_id"], job["owner_user_id"], job["tenant_id"], "queued", error=str(exc))
            store.defer_retry(job["run_id"], retry_at)
        run_attempts_total.labels("retry").inc()
        return
    finally:
        # 取消轮询任务是本次执行的必须清理项。
        cancellation_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancellation_task

    run_attempts_total.labels("completed").inc()

    tool_results = result.get("tool_results", [])
    # 与同步 API 保持相同输出契约：最后结果作为总输出，确认请求隐藏输出。
    output = tool_results[-1].get("output") if tool_results else None
    confirmation_token = None
    status = result.get("status") or "completed"
    if isinstance(output, dict) and output.get("status") == "confirmation_required":
        status = "awaiting_confirmation"
        confirmation_token = output["confirmation_token"]
        output = None
    if postgres:
        await store.update_run(job["run_id"], job["owner_user_id"], job["tenant_id"], status, str(output) if output is not None else None, confirmation_token, total_tokens=tokens, total_cost=cost)
    else:
        manager.update(job["run_id"], job["owner_user_id"], job["tenant_id"], status, str(output) if output is not None else None, confirmation_token, total_tokens=tokens, total_cost=cost)
    cancellation_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await cancellation_task


async def worker_loop(
    store=None,
    poll_interval: float = 0.5,
    stop_event: asyncio.Event | None = None,
    worker_id: str | None = None,
    lease_seconds: int = 60,
    services: Any | None = None,
) -> None:
    store = store or create_job_store()
    if isinstance(store, PostgresStore):
        # PostgreSQL worker 使用数据库原子 claim，天然支持多副本。
        worker_id = worker_id or uuid.uuid4().hex
        while stop_event is None or not stop_event.is_set():
            job = await store.claim_next_run(worker_id, lease_seconds)
            if job is None:
                # 无任务时短轮询，避免数据库压力过高。
                await asyncio.sleep(poll_interval)
                continue
            try:
                await run_one(job, store, worker_id, lease_seconds, services)
            except Exception:
                # run_one 内部会处理常规失败；这里兜底记录意外错误。
                logger.exception("postgres_worker_run_failed", run_id=job.get("run_id"))
        return

    manager = JobManager(store)
    worker_id = worker_id or uuid.uuid4().hex
    heartbeat_task: asyncio.Task | None = None
    while stop_event is None or not stop_event.is_set():
        job = manager.claim_next(worker_id, lease_seconds)
        if job is None:
            await asyncio.sleep(poll_interval)
            continue
        heartbeat_stop = asyncio.Event()

        run_id = job["run_id"]

        async def heartbeat(
            stop: asyncio.Event = heartbeat_stop,
            active_run_id: str = run_id,
            active_worker_id: str = worker_id,
            active_manager: JobManager = manager,
            active_lease_seconds: int = lease_seconds,
        ) -> None:
            while not stop.is_set():
                # 定期续租，worker 崩溃后租约过期可被其他实例接管。
                active_manager.renew_lease(active_run_id, active_worker_id, active_lease_seconds)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=max(1, active_lease_seconds // 3))
                except TimeoutError:
                    pass

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            await run_one(job, store, worker_id, lease_seconds, services)
        finally:
            heartbeat_stop.set()
            await asyncio.sleep(0)
            if heartbeat_task:
                heartbeat_task.cancel()
    if heartbeat_task:
        heartbeat_task.cancel()
