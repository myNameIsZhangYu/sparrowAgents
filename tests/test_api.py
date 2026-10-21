import hashlib

import httpx

from person_agents.api.app import app
from person_agents.middleware import _confirmations


async def api_client(monkeypatch):
    monkeypatch.setattr(
        "person_agents.config.settings.settings.api_key_hash",
        hashlib.sha256(b"test-key").hexdigest(),
        raising=False,
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_async_run_requires_writable_store(monkeypatch, tmp_path):
    from person_agents.api.app import _jobs
    from person_agents.memory.sqlite_store import SQLiteStore

    _jobs.store = SQLiteStore(db_path=tmp_path / "api.db")


async def test_healthz(monkeypatch) -> None:
    async with await api_client(monkeypatch) as api:
        response = await api.get("/healthz")
        assert response.json() == {"status": "ok"}


async def test_web_console(monkeypatch) -> None:
    async with await api_client(monkeypatch) as api:
        response = await api.get("/")
        assert response.status_code == 200
        assert "personAgents 智能体控制台" in response.text


async def test_run_requires_key(monkeypatch) -> None:
    async with await api_client(monkeypatch) as api:
        response = await api.post("/runs", json={"prompt": "hello"})
        assert response.status_code == 401


async def test_run_returns_service_unavailable_for_llm_outage(monkeypatch) -> None:
    from person_agents.llm.fallback import LLMFallbackError

    async def unavailable_run(*args, **kwargs):
        raise LLMFallbackError("All models in fallback chain failed: Connection error.")

    monkeypatch.setattr("person_agents.api.app._run_graph", unavailable_run)
    async with await api_client(monkeypatch) as api:
        response = await api.post(
            "/runs",
            json={"prompt": "hello"},
            headers={"X-API-Key": "test-key"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "LLM providers unavailable"}


async def test_confirmation_endpoints(monkeypatch) -> None:
    async with await api_client(monkeypatch) as api:
        digest = hashlib.sha256(b"test-key").hexdigest()
        context_user = f"user_{digest[:24]}"
        confirmation = _confirmations.create(context_user, "default", "filesystem", {"operation": "write"})
        confirm = await api.post(f"/confirmations/{confirmation.token}/confirm", headers={"X-API-Key": "test-key"})
        assert confirm.status_code == 200
        reject = await api.post(f"/confirmations/{confirmation.token}/reject", headers={"X-API-Key": "test-key"})
        assert reject.status_code == 200


async def test_async_run_and_cancel(monkeypatch, tmp_path) -> None:
    from person_agents.api.app import _jobs
    from person_agents.memory.sqlite_store import SQLiteStore

    _jobs.store = SQLiteStore(db_path=tmp_path / "cancel.db")
    _jobs.registry.store = _jobs.store
    async with await api_client(monkeypatch) as api:
        accepted = await api.post("/runs/async", json={"prompt": "hello"}, headers={"X-API-Key": "test-key"})
        assert accepted.status_code == 202
        run_id = accepted.json()["run_id"]
        canceled = await api.post(f"/runs/{run_id}/cancel", headers={"X-API-Key": "test-key"})
        assert canceled.status_code == 200
        assert canceled.json()["status"] == "canceled"
