import concurrent.futures
from datetime import datetime
from typing import Protocol

from person_agents.config.settings import settings
from person_agents.memory.postgres_store import PostgresStore
from person_agents.memory.sqlite_store import SQLiteStore


class JobStore(Protocol):
    def create_run(
        self,
        run_id: str,
        prompt: str,
        owner_user_id: str,
        tenant_id: str,
        session_id: str,
        status: str = "queued",
        provenance: dict[str, str] | None = None,
    ) -> None: ...

    def update_run(
        self,
        run_id: str,
        owner_user_id: str,
        tenant_id: str,
        status: str,
        output: str | None = None,
        confirmation_token: str | None = None,
        error: str | None = None,
        total_tokens: int = 0,
        total_cost: float = 0.0,
    ) -> bool: ...

    def get_run(self, run_id: str, owner_user_id: str, tenant_id: str) -> dict | None: ...

    def claim_next_run(self, worker_id: str, lease_seconds: int = 60) -> dict | None: ...

    def renew_run_lease(self, run_id: str, worker_id: str, lease_seconds: int = 60) -> bool: ...


class AsyncPostgresJobAdapter:
    def __init__(self, store: PostgresStore | None = None):
        self.store = store or PostgresStore()

    def create_run(self, *args, **kwargs):
        return run_coroutine(self.store.create_run(*args, **kwargs))

    def update_run(self, *args, **kwargs):
        return run_coroutine(self.store.update_run(*args, **kwargs))

    def get_run(self, *args, **kwargs):
        return run_coroutine(self.store.get_run(*args, **kwargs))

    def claim_next_run(self, *args, **kwargs):
        return run_coroutine(self.store.claim_next_run(*args, **kwargs))

    def renew_run_lease(self, *args, **kwargs):
        return run_coroutine(self.store.renew_run_lease(*args, **kwargs))

    def queue_depth(self) -> int:
        return run_coroutine(self.store.depth("queued"))

    def running_depth(self) -> int:
        return run_coroutine(self.store.depth("running"))

    def dead_letter_depth(self) -> int:
        return run_coroutine(self.store.depth("dead_letter"))

    def defer_retry(self, run_id: str, next_retry_at: datetime) -> bool:
        return run_coroutine(self.store.defer_run_retry(run_id, next_retry_at))


def run_coroutine(awaitable):
    import asyncio
    import contextvars

    loop = asyncio.new_event_loop()
    try:
        context = contextvars.copy_context()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(context.run, loop.run_until_complete, awaitable).result()
    finally:
        loop.close()


def create_job_store():
    if settings.postgres_dsn:
        return PostgresStore()
    return SQLiteStore()
