from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from person_agents.config.settings import settings
from person_agents.governance.audit import AuditLogger, AuditRedactor


class AuditStore(Protocol):
    async def arecord(
        self,
        agent: str,
        action: str,
        input_data: str = "",
        output_data: str = "",
        status: str = "success",
        error: str = "",
        request_id: str | None = None,
        owner_user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None: ...

    async def purge_expired(self, retention_days: int) -> int: ...


class PostgresAuditStore:
    SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id BIGSERIAL PRIMARY KEY,
        timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
        agent TEXT NOT NULL,
        action TEXT NOT NULL,
        input TEXT,
        output TEXT,
        status TEXT NOT NULL,
        error TEXT,
        request_id TEXT,
        owner_user_id TEXT,
        tenant_id TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_audit_log_tenant_time
        ON audit_log(tenant_id, timestamp DESC);
    """,
    )

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or settings.postgres_dsn
        self.engine = create_async_engine(self._dsn)
        self._schema_ready = False

    async def record(
        self,
        agent: str,
        action: str,
        input_data: str = "",
        output_data: str = "",
        status: str = "success",
        error: str = "",
        request_id: str | None = None,
        owner_user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        async with self.engine.begin() as conn:
            if not self._schema_ready:
                for statement in self.SCHEMA_STATEMENTS:
                    await conn.execute(text(statement))
                self._schema_ready = True
            await conn.execute(
                text(
                    """INSERT INTO audit_log (
                        agent, action, input, output, status, error,
                        request_id, owner_user_id, tenant_id
                    ) VALUES (:agent, :action, :input, :output, :status, :error,
                        :request_id, :owner_user_id, :tenant_id)"""
                ),
                {
                    "agent": agent,
                    "action": action,
                    "input": AuditRedactor.redact(input_data),
                    "output": AuditRedactor.redact(output_data),
                    "status": status,
                    "error": AuditRedactor.redact(error),
                    "request_id": request_id,
                    "owner_user_id": owner_user_id,
                    "tenant_id": tenant_id,
                },
            )

    async def purge_expired(self, retention_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with self.engine.begin() as conn:
            result = await conn.execute(text("DELETE FROM audit_log WHERE timestamp < :cutoff"), {"cutoff": cutoff})
            return result.rowcount

    arecord = record


def create_audit_store() -> AuditStore:
    if settings.postgres_dsn:
        return PostgresAuditStore()
    return AuditLogger()
