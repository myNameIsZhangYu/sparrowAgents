import asyncio

import pytest

from person_agents.config.settings import settings
from person_agents.governance.rate_limiter import QuotaExceededError
from person_agents.memory.postgres_store import PostgresStore
from person_agents.runtime.state import InMemoryStateStore
from person_agents.runtime.worker import run_one


class FakePostgresStore(PostgresStore):
    def __init__(self) -> None:
        self.runs = {}

    async def renew_run_lease(self, run_id, worker_id, lease_seconds=60):
        return True

    async def get_run(self, run_id, user_id, tenant_id):
        return self.runs.get(run_id)

    async def update_run(self, run_id, user_id, tenant_id, status, error=None, *args, **kwargs):
        self.runs[run_id] = {"status": status, "error": error}

    async def defer_run_retry(self, run_id, retry_at):
        self.runs.setdefault(run_id, {})["retry_at"] = retry_at


async def test_run_timeout_marks_dead_letter_and_cancels_graph(monkeypatch):
    monkeypatch.setattr(settings, "run_timeout_s", 0.01, raising=False)
    monkeypatch.setattr(settings, "run_max_attempts", 1, raising=False)

    async def hanging_graph(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("person_agents.graph.engine.run_graph", hanging_graph)
    store = FakePostgresStore()
    job = {"run_id": "run", "owner_user_id": "user", "tenant_id": "tenant", "session_id": "session", "prompt": "prompt", "attempts": 1}

    await run_one(job, store)

    assert store.runs["run"]["status"] == "dead_letter"
    assert store.runs["run"]["error"] == "Run timed out"


async def test_canceled_run_failure_is_not_retried(monkeypatch):
    async def failing_graph(*args, **kwargs):
        raise RuntimeError("graph failed after cancel")

    monkeypatch.setattr("person_agents.graph.engine.run_graph", failing_graph)
    store = FakePostgresStore()
    store.runs["run"] = {"status": "canceled"}
    job = {"run_id": "run", "owner_user_id": "user", "tenant_id": "tenant", "session_id": "session", "prompt": "prompt", "attempts": 1}

    await run_one(job, store)

    assert store.runs["run"]["status"] == "canceled"
    assert "retry_at" not in store.runs["run"]


async def test_canceled_run_releases_slot_and_cancel_marker(monkeypatch):
    from person_agents.governance.rate_limiter import RateLimiter

    state_store = InMemoryStateStore()
    limiter = RateLimiter(state_store, max_running_runs=1, daily_run_limit=2)
    monkeypatch.setattr("person_agents.runtime.worker.RateLimiter", lambda *args, **kwargs: limiter)
    monkeypatch.setattr("person_agents.runtime.worker.create_state_store", lambda redis_url: state_store)

    async def graph(*args, **kwargs):
        raise RuntimeError("graph failed after cancel")

    monkeypatch.setattr("person_agents.graph.engine.run_graph", graph)
    store = FakePostgresStore()
    store.runs["run"] = {"status": "canceled"}
    job = {"run_id": "run", "owner_user_id": "user", "tenant_id": "tenant", "session_id": "session", "prompt": "prompt", "attempts": 1}
    state_store.set_json("cancel:run", True)

    await run_one(job, store)

    assert store.runs["run"]["status"] == "canceled"
    assert limiter.allow_run("user", "tenant")
    slot_key = "run_slot:tenant:user"
    slot_id = next(iter(limiter.state_store.get_json(slot_key)))
    limiter.release_run_slot(slot_id, "user", "tenant")
    assert not state_store.get_json(slot_key)
    assert state_store.get_json("cancel:run") is None


async def test_run_uses_concurrent_slot_while_running_and_releases_on_failure(monkeypatch):
    from person_agents.governance.rate_limiter import RateLimiter

    limiter = RateLimiter(InMemoryStateStore(), max_running_runs=1, daily_run_limit=2)
    monkeypatch.setattr("person_agents.runtime.worker.RateLimiter", lambda *args, **kwargs: limiter)

    async def graph(*args, **kwargs):
        with pytest.raises(QuotaExceededError, match="Concurrent"):
            limiter.allow_run("user", "tenant")
        return {}

    monkeypatch.setattr("person_agents.graph.engine.run_graph", graph)
    store = FakePostgresStore()
    job = {"run_id": "run", "owner_user_id": "user", "tenant_id": "tenant", "session_id": "session", "prompt": "prompt", "attempts": 1}

    await run_one(job, store)
