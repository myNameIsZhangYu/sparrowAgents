
import pytest

from person_agents.governance.policy import Permission, PolicyEngine
from person_agents.governance.rate_limiter import RateLimiter


class TestAuditRedaction:
    def test_redacts_sensitive_fields(self, tmp_path) -> None:
        from person_agents.governance.audit import AuditLogger

        logger = AuditLogger(tmp_path / "audit.db")
        logger.record(
            "middleware",
            "test",
            "contact user@example.com or 13800138000",
            "bearer abcdefghijklmnopqrstuvwxyz123456",
            error="card 4111111111111111",
        )
        row = logger.connect().execute("SELECT input,output,error FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        assert "user@example.com" not in row[0]
        assert "13800138000" not in row[0]
        assert "abcdefghijklmnopqrstuvwxyz123456" not in row[1]
        assert "4111111111111111" not in row[2]
        assert row[0].count("[REDACTED]") == 2

    def test_retention_purge(self, tmp_path) -> None:
        from datetime import UTC, datetime, timedelta

        from person_agents.governance.audit import AuditLogger

        logger = AuditLogger(tmp_path / "retention.db")
        logger.record("middleware", "old")
        old_timestamp = (datetime.now(UTC) - timedelta(days=91)).isoformat()
        logger.connect().execute("UPDATE audit_log SET timestamp=?", (old_timestamp,))
        logger._conn.commit()
        assert logger.purge_expired(90) == 1


class TestPolicyEngine:
    def test_read_allowed_by_default(self) -> None:
        engine = PolicyEngine()
        assert engine.check(Permission.READ) is True

    def test_shell_denied_by_default(self) -> None:
        engine = PolicyEngine()
        assert engine.check(Permission.SHELL) is False

    def test_grant_revoke(self) -> None:
        engine = PolicyEngine()
        engine.grant(Permission.WRITE)
        assert engine.check(Permission.WRITE) is True
        engine.revoke(Permission.WRITE)
        assert engine.check(Permission.WRITE) is False

    def test_block_rm_rf(self) -> None:
        engine = PolicyEngine()
        ok, _ = engine.check_command("rm -rf /")
        assert ok is False

    def test_block_rm_rf_variant(self) -> None:
        engine = PolicyEngine()
        ok, _ = engine.check_command("rm  -r -f /")
        assert ok is False

    def test_block_git_force(self) -> None:
        engine = PolicyEngine()
        ok, _ = engine.check_command("git push --force origin main")
        assert ok is False

    def test_allow_safe_command(self) -> None:
        engine = PolicyEngine()
        ok, _ = engine.check_command("ls -la /tmp")
        assert ok is True


class TestRateLimiter:
    def test_allow_within_capacity(self) -> None:
        limiter = RateLimiter(llm_capacity=3, llm_refill_rate=0.1)
        assert limiter.allow_llm() is True
        assert limiter.allow_llm() is True
        assert limiter.allow_llm() is True
        assert limiter.allow_llm() is False

    def test_llm_usage_quota(self) -> None:
        from types import SimpleNamespace

        from person_agents.runtime.state import InMemoryStateStore

        limiter = RateLimiter(InMemoryStateStore())
        response = SimpleNamespace(content="hello world", prompt_tokens=900000, completion_tokens=100000)
        tokens, _ = limiter.record_llm_usage(response, "user", "tenant")
        assert tokens == 1000000
        with pytest.raises(RuntimeError, match="Daily token quota"):
            limiter.record_llm_usage(response, "user", "tenant")
async def test_practical_filesystem_operations_are_governed(tmp_path, monkeypatch):
    from person_agents.tools.registry import ToolRegistry, register_default_tools

    registry = ToolRegistry()
    register_default_tools(registry, workspace_root=str(tmp_path))
    definition = registry.get("filesystem")

    assert definition.requires_confirm is True
    assert set(definition.parameters["properties"]["operation"]["enum"]) >= {
        "append", "copy", "move", "mkdir", "search"
    }

    result = await definition.handler({"operation": "copy", "path": "a.txt", "destination": "../escape"})
    assert result["status"] == "error"
