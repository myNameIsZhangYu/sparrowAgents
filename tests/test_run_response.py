from types import SimpleNamespace

import httpx

from person_agents.api.app import app


async def test_get_run_includes_run_id(monkeypatch, tmp_path) -> None:
    from person_agents.api.app import _jobs
    from person_agents.memory.sqlite_store import SQLiteStore

    _jobs.store = SQLiteStore(db_path=tmp_path / "runs.db")
    _jobs.registry.store = _jobs.store
    owner_user_id = "user_"
    run_id = _jobs.create("hello", owner_user_id, "default", "session", provenance={})

    transport = httpx.ASGITransport(app=app)
    async def resolve_context(api_key):
        return SimpleNamespace(user_id=owner_user_id, tenant_id="default", session_id="session", role="user")

    monkeypatch.setattr("person_agents.api.app.resolve_request_context", resolve_context)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get(f"/runs/{run_id}", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    assert response.json()["run_id"] == run_id
