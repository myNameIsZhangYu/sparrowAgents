from person_agents.runtime.startup import wait_for_migrations


async def test_wait_for_migrations_noop_without_dsn(monkeypatch) -> None:
    monkeypatch.setattr("person_agents.config.settings.settings.postgres_dsn", "")
    await wait_for_migrations()


async def test_wait_for_migrations_bootstraps_stores(monkeypatch) -> None:
    calls = []

    class FakeStore:
        async def bootstrap(self):
            calls.append("bootstrap")

        async def record(self, *args, **kwargs):
            calls.append("audit")

    monkeypatch.setattr("person_agents.runtime.startup.PostgresStore", lambda dsn: FakeStore())
    monkeypatch.setattr("person_agents.runtime.startup.PostgresAuditStore", lambda dsn: FakeStore())
    monkeypatch.setattr("person_agents.runtime.startup.PostgresCredentialStore", lambda dsn: FakeStore())
    monkeypatch.setattr("person_agents.runtime.startup.PostgresConfigRegistry", lambda dsn: FakeStore())
    await wait_for_migrations("postgresql+asyncpg://u:p@db/test")
    assert calls == ["bootstrap", "audit", "bootstrap", "bootstrap"]
