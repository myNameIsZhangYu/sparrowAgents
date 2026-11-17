import hashlib

import httpx

from person_agents.api.app import app


async def test_canceled_async_run_releases_slot(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "person_agents.config.settings.settings.api_key_hash",
        hashlib.sha256(b"test-key").hexdigest(),
        raising=False,
    )
    from person_agents.api.app import _jobs, _rate_limiter
    from person_agents.memory.sqlite_store import SQLiteStore

    _jobs.store = SQLiteStore(db_path=tmp_path / "cancel.db")
    _jobs.registry.store = _jobs.store
    monkeypatch.setattr(_rate_limiter, "check_usage", lambda *args, **kwargs: None)
    def allow_run(owner_user_id, tenant_id):
        return run_id_holder[0]

    run_id_holder = [""]
    monkeypatch.setattr(_rate_limiter, "allow_run", allow_run)

    releases = []
    original_release = _rate_limiter.release_run_slot

    def release(slot_id, owner_user_id, tenant_id):
        releases.append((slot_id, owner_user_id, tenant_id))
        original_release(slot_id, owner_user_id, tenant_id)

    monkeypatch.setattr(_rate_limiter, "release_run_slot", release)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        accepted = await api.post(
            "/runs/async",
            json={"prompt": "cancel"},
            headers={"X-API-Key": "test-key"},
        )
        run_id = accepted.json()["run_id"]
        canceled = await api.post(
            f"/runs/{run_id}/cancel",
            headers={"X-API-Key": "test-key"},
        )

    assert canceled.status_code == 200
    context_user = hashlib.sha256(b"test-key").hexdigest()
    context_user = f"user_{context_user[:24]}"
    assert releases == [(run_id_holder[0], context_user, "default")]
