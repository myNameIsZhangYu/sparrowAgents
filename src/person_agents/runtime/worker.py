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
    state_store = create_state_store(settings.redis_url)
    rate_limiter = RateLimiter(state_store)

    postgres = isinstance(store, PostgresStore)
    if not postgres:
        manager = JobManager(store)

    async def poll_cancellation() -> None:
        while True:
            if state_store.get_json(f"cancel:{job['run_id']}"):
                raise TimeoutError("Run canceled")
            await asyncio.sleep(0.2)

    cancellation_task = asyncio.create_task(poll_cancellation())
    if postgres:
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
            graph_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await graph_task
            raise TimeoutError("Run canceled")
        if graph_task not in done:
            graph_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await graph_task
            raise TimeoutError("Run timed out")
        cancellation_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancellation_task
        result = graph_task.result()
        llm_result = result.get("llm_response")
        tokens, cost = rate_limiter.record_llm_usage(llm_result, job["owner_user_id"], job["tenant_id"]) if llm_result else (0, 0.0)
    except (RuntimeError, ValueError, TimeoutError, OSError) as exc:
        attempts = int(job.get("attempts", 1))
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
            if postgres:
                await store.update_run(job["run_id"], job["owner_user_id"], job["tenant_id"], "dead_letter", error=str(exc))
            else:
                manager.update(job["run_id"], job["owner_user_id"], job["tenant_id"], "dead_letter", error=str(exc))
            run_attempts_total.labels("dead_letter").inc()
            run_dead_letter_total.inc()
            return
        retry_at = datetime.now(UTC) + timedelta(seconds=settings.run_retry_backoff_seconds * (2 ** (attempts - 1)))
        if postgres:
            await store.update_run(job["run_id"], job["owner_user_id"], job["tenant_id"], "queued", error=str(exc))
            await store.defer_run_retry(job["run_id"], retry_at)
        else:
            manager.update(job["run_id"], job["owner_user_id"], job["tenant_id"], "queued", error=str(exc))
            store.defer_retry(job["run_id"], retry_at)
        run_attempts_total.labels("retry").inc()
        return
    finally:
        cancellation_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancellation_task

    run_attempts_total.labels("completed").inc()

    tool_results = result.get("tool_results", [])
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
        worker_id = worker_id or uuid.uuid4().hex
        while stop_event is None or not stop_event.is_set():
            job = await store.claim_next_run(worker_id, lease_seconds)
            if job is None:
                await asyncio.sleep(poll_interval)
                continue
            try:
                await run_one(job, store, worker_id, lease_seconds, services)
            except Exception:
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
