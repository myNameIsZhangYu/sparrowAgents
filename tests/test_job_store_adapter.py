from datetime import UTC, datetime, timedelta

from person_agents.runtime.job_store import AsyncPostgresJobAdapter


class FakePostgresStore:
    def __init__(self):
        self.created = []
        self.claimed = []

    async def create_run(self, run_id, prompt, owner_user_id, tenant_id, session_id, status="queued", provenance=None):
        self.created.append((run_id, prompt, owner_user_id, tenant_id, session_id))

    async def update_run(self, run_id, owner_user_id, tenant_id, status, output=None, confirmation_token=None, error=None, total_tokens=0, total_cost=0.0):
        return status == "completed"

    async def get_run(self, run_id, owner_user_id, tenant_id):
        return {"run_id": run_id, "owner_user_id": owner_user_id, "tenant_id": tenant_id}

    async def claim_next_run(self, worker_id, lease_seconds=60):
        self.claimed.append((worker_id, lease_seconds))
        return {"run_id": "run", "attempts": 1}

    async def renew_run_lease(self, run_id, worker_id, lease_seconds=60):
        return True

    async def defer_run_retry(self, run_id, next_retry_at):
        return next_retry_at > datetime.now(UTC)

    async def depth(self, status):
        return {"queued": 2, "running": 1, "dead_letter": 0}[status]


def test_async_postgres_job_adapter_semantics():
    fake = FakePostgresStore()
    adapter = AsyncPostgresJobAdapter(fake)

    adapter.create_run("run", "prompt", "user", "tenant", "session")
    assert fake.created == [("run", "prompt", "user", "tenant", "session")]
    assert adapter.update_run("run", "user", "tenant", "completed") is True
    assert adapter.get_run("run", "user", "tenant")["run_id"] == "run"
    assert adapter.claim_next_run("worker", 30)["run_id"] == "run"
    assert fake.claimed == [("worker", 30)]
    assert adapter.renew_run_lease("run", "worker") is True
    assert adapter.defer_retry("run", datetime.now(UTC) + timedelta(seconds=1)) is True
    assert (adapter.queue_depth(), adapter.running_depth(), adapter.dead_letter_depth()) == (2, 1, 0)
