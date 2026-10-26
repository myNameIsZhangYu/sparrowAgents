from pathlib import Path

import pytest

from person_agents.security.auth import (
    SecurityError,
    resolve_registry_context,
    resolve_request_context,
)
from person_agents.security.credentials import CredentialManager


def test_credential_lifecycle(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "credentials.db"
    manager = CredentialManager(db)
    key_id, plaintext = manager.create("tenant-a", "user-a")
    assert manager.verify(plaintext).tenant_id == "tenant-a"
    assert manager.verify(plaintext).user_id == "user-a"

    rotated = manager.rotate(key_id)
    assert rotated is not None
    assert rotated[0] != key_id
    assert manager.verify(plaintext) is None
    assert manager.verify(rotated[1]) is not None

    manager.revoke(rotated[0])
    assert manager.verify(rotated[1]) is None


def test_registry_identity_and_legacy_fallback(tmp_path: Path, monkeypatch) -> None:
    import hashlib


    manager = CredentialManager(tmp_path / "credentials.db")
    _, plaintext = manager.create("tenant-b", "user-b")
    context = resolve_registry_context(plaintext, tmp_path / "credentials.db")
    assert context.tenant_id == "tenant-b"
    assert context.user_id == "user-b"

    monkeypatch.setattr(
        "person_agents.config.settings.settings.api_key_hash",
        hashlib.sha256(b"legacy").hexdigest(),
        raising=False,
    )
    monkeypatch.setattr(
        "person_agents.security.auth.CredentialManager",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )
    context = resolve_request_context("legacy")
    assert context.user_id.startswith("user_")
    with pytest.raises(SecurityError):
        resolve_request_context("bad")
