import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import structlog

from person_agents.config.settings import settings

logger = structlog.get_logger()


class AuditRedactor:
    PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
        re.compile(r"\b(?:\+?\d[\s-]?){9,14}\d\b"),
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+\b", re.IGNORECASE),
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
    ]

    @classmethod
    def redact(cls, value: str | None) -> str:
        if not value:
            return ""
        result = value
        for pattern in cls.PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        return result[:2000]


class AuditLogger:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.sqlite_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                input TEXT,
                output TEXT,
                status TEXT NOT NULL,
                error TEXT,
                request_id TEXT,
                owner_user_id TEXT,
                tenant_id TEXT
            )""")
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(audit_log)")}
            for column in ("request_id", "owner_user_id", "tenant_id"):
                if column not in columns:
                    self._conn.execute(f"ALTER TABLE audit_log ADD COLUMN {column} TEXT")
        return self._conn

    def record(
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
        conn = self.connect()
        conn.execute(
            "INSERT INTO audit_log (timestamp, agent, action, input, output, status, error, request_id, owner_user_id, tenant_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                datetime.now(UTC).isoformat(),
                agent,
                action,
                AuditRedactor.redact(input_data),
                AuditRedactor.redact(output_data),
                status,
                AuditRedactor.redact(error),
                request_id,
                owner_user_id,
                tenant_id,
            ),
        )
        conn.commit()

    def purge_expired(self, retention_days: int) -> int:
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        cursor = self.connect().execute("DELETE FROM audit_log WHERE timestamp < ?", (cutoff.isoformat(),))
        self._conn.commit()
        return cursor.rowcount

    async def arecord(self, *args, **kwargs) -> None:
        self.record(*args, **kwargs)

    async def apurge_expired(self, retention_days: int) -> int:
        return self.purge_expired(retention_days)
