from person_agents.governance.audit import AuditLogger
from person_agents.governance.audit_store import AuditStore, PostgresAuditStore, create_audit_store


def test_audit_store_protocol_is_async_compatible() -> None:
    logger = AuditLogger()
    assert logger.arecord is not None
    assert logger.apurge_expired is not None

    store: AuditStore = logger
    assert store.arecord is not None


def test_postgres_audit_redacts_and_uses_dsn(monkeypatch) -> None:
    monkeypatch.setattr("person_agents.config.settings.settings.postgres_dsn", "postgresql+asyncpg://u:p@db/test")
    store = PostgresAuditStore()
    assert store._dsn == "postgresql+asyncpg://u:p@db/test"


def test_create_audit_store_selector(monkeypatch) -> None:
    monkeypatch.setattr("person_agents.config.settings.settings.postgres_dsn", "")
    assert isinstance(create_audit_store(), AuditLogger)
    monkeypatch.setattr("person_agents.config.settings.settings.postgres_dsn", "postgresql+asyncpg://u:p@db/test")
    assert isinstance(create_audit_store(), PostgresAuditStore)


async def test_postgres_audit_schema_is_initialized_once(monkeypatch) -> None:
    store = PostgresAuditStore.__new__(PostgresAuditStore)
    store._dsn = "postgresql+asyncpg://user:pass@db/test"
    store.engine = object()
    store._schema_ready = False
    executed = []

    class Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, statement, *args, **kwargs):
            executed.append(str(statement))

    monkeypatch.setattr(store, "engine", type("Engine", (), {"begin": staticmethod(lambda: Conn())}))

    for _ in range(2):
        await store.record("agent", "action")

    assert len(executed) == 4
