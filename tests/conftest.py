from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def disable_runtime_integrations(monkeypatch):
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    from person_agents.config.settings import settings

    settings.testing = True
    settings.langfuse_enabled = False
    from person_agents.observability import langfuse

    langfuse._client = None
    langfuse._tracing_disabled = True
    data_dir = Path("/tmp/person-agents-test-data").absolute()
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "sqlite_path", data_dir / "memory.db")
    monkeypatch.setattr(settings, "audit_db_path", data_dir / "audit.db")
    from person_agents.middleware import _audit

    if hasattr(_audit, "db_path"):
        _audit.db_path = data_dir / "audit.db"
        _audit._conn = None
    monkeypatch.setattr(settings, "chroma_path", data_dir / "chroma")
    monkeypatch.setattr(
        settings,
        "chroma_model_cache_path",
        Path("/home/zhangyu/.cache/chroma/onnx_models/all-MiniLM-L6-v2"),
    )
