import asyncio

from person_agents.memory.postgres_store import PostgresStore
from person_agents.runtime.config_repository import PostgresConfigRegistry
from person_agents.security.credential_store import PostgresCredentialStore


def test_postgres_credential_store_uses_dsn_and_digest() -> None:
    store = PostgresCredentialStore("postgresql+asyncpg://u:p@db/test")
    assert store._dsn == "postgresql+asyncpg://u:p@db/test"
    assert store.digest("key") == __import__("hashlib").sha256(b"key").hexdigest()


def test_postgres_config_registry_versioning() -> None:
    registry = PostgresConfigRegistry("postgresql+asyncpg://u:p@db/test")
    assert len(registry.version_id("prompt")) == 24


class FakeResult:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def first(self):
        return self._row


class FakeConnection:
    def __init__(self, queue, rows):
        self.queue = queue
        self.rows = rows

    async def execute(self, query, params=None):
        statement = str(query).strip().lower()
        if "update runs set status='queued'" in statement:
            self.queue.append(("reclaim", params))
            return FakeResult(rowcount=1)
        if "update runs set status='running'" in statement:
            self.queue.append(("claim", params))
            return FakeResult(row=self.rows.pop(0) if self.rows else None, rowcount=1)
        return FakeResult()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeEngine:
    def __init__(self, rows):
        self.rows = rows
        self.queue = []

    def begin(self):
        return FakeConnection(self.queue, self.rows)


def test_claim_next_run_reclaims_expired_leases_before_claiming() -> None:
    store = PostgresStore("postgresql+asyncpg://u:p@db/test")
    row = type(
        "Row",
        (),
        {"_mapping": {"run_id": "run", "tenant_id": "t", "owner_user_id": "u", "session_id": "s", "prompt": "p", "attempts": 1}},
    )()
    store.engine = FakeEngine([row])

    job = asyncio.run(store.claim_next_run("worker", 30))

    assert job["run_id"] == "run"
    assert store.engine.queue[0] == ("reclaim", None)
    assert store.engine.queue[1][0] == "claim"
    assert store.engine.queue[1][1]["worker_id"] == "worker"
