import hashlib

import pytest

from person_agents.config.settings import settings
from person_agents.security.auth import resolve_request_context


def test_admin_hash_sets_admin_role(monkeypatch):
    monkeypatch.setattr(settings, "api_key_hash", hashlib.sha256(b"admin-key").hexdigest(), raising=False)
    monkeypatch.setattr(settings, "admin_api_key_hash", hashlib.sha256(b"admin-key").hexdigest(), raising=False)

    context = resolve_request_context("admin-key")

    assert context.role == "admin"


async def test_admin_denial_is_audited(monkeypatch, tmp_path):
    from person_agents.api.admin import require_admin
    from person_agents.governance.audit import AuditLogger

    audit = AuditLogger(tmp_path / "audit.db")
    monkeypatch.setattr("person_agents.api.admin._audit", audit)
    monkeypatch.setattr(settings, "admin_api_key_hash", hashlib.sha256(b"admin-key").hexdigest(), raising=False)

    with pytest.raises(Exception, match="401"):
        await require_admin(None)

    with audit.connect() as conn:
        row = conn.execute("SELECT status FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "denied"
