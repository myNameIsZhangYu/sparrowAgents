import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from person_agents.config.settings import settings

logger = structlog.get_logger()

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    owner_user_id TEXT NOT NULL DEFAULT 'local',
    tenant_id TEXT NOT NULL DEFAULT 'default',
    value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    ended_at TEXT,
    total_tokens INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_owner_key ON memories(tenant_id, owner_user_id, key);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    output TEXT,
    confirmation_token TEXT,
    error TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    agent_version TEXT,
    prompt_version TEXT,
    tool_config_version TEXT,
    policy_version TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_runs_owner ON runs(tenant_id, owner_user_id, status);
CREATE TABLE IF NOT EXISTS config_versions (
    kind TEXT NOT NULL,
    version_id TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (kind, version_id)
);
CREATE TABLE IF NOT EXISTS agent_collaboration_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    task_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(session_id, event_type, from_agent, to_agent, task_id, correlation_id, payload_json)
);
CREATE INDEX IF NOT EXISTS idx_agent_collaboration_session
    ON agent_collaboration_events(session_id, correlation_id);
"""

MIGRATIONS = [
    # 兼容旧库的轻量迁移；新库由 SCHEMA 直接创建这些列。
    "ALTER TABLE runs ADD COLUMN lease_owner TEXT",
    "ALTER TABLE runs ADD COLUMN lease_expires_at TEXT",
    "ALTER TABLE runs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE runs ADD COLUMN next_retry_at TEXT",
    "ALTER TABLE runs ADD COLUMN agent_version TEXT",
    "ALTER TABLE runs ADD COLUMN prompt_version TEXT",
    "ALTER TABLE runs ADD COLUMN tool_config_version TEXT",
    "ALTER TABLE runs ADD COLUMN policy_version TEXT",
    "ALTER TABLE runs ADD COLUMN metadata_json TEXT",
    "ALTER TABLE runs ADD COLUMN total_tokens INTEGER DEFAULT 0",
    "ALTER TABLE runs ADD COLUMN total_cost_usd REAL DEFAULT 0.0",
]


class SQLiteStore:
    def __init__(self, db_path: Path | None = None):
        # 单机路径保留默认 settings，测试可传临时库路径隔离。
        self.db_path = db_path or settings.sqlite_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=10)
            # WAL 提高并发读写能力；外键约束保证后续扩展的数据一致性。
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            for migration in MIGRATIONS:
                try:
                    # ALTER TABLE 无 IF NOT EXISTS；重复迁移错误可安全忽略。
                    self._conn.execute(migration)
                except sqlite3.OperationalError:
                    pass
            self.connect_registry()
        return self._conn

    def connect_registry(self) -> None:
        self.connect().execute(
            """CREATE TABLE IF NOT EXISTS config_versions (
                kind TEXT NOT NULL,
                version_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                PRIMARY KEY (kind, version_id)
            )"""
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def upsert(self, key: str, value: str, category: str = "fact", owner_user_id: str = "local", tenant_id: str = "default") -> None:
        conn = self.connect()
        # 唯一键包含 tenant + owner + key，实现记忆隔离和幂等更新。
        conn.execute(
            """INSERT INTO memories (key, owner_user_id, tenant_id, value, category) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id, owner_user_id, key) DO UPDATE SET value=excluded.value, category=excluded.category,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
            (key, owner_user_id, tenant_id, value, category),
        )
        conn.commit()

    def get(self, key: str, owner_user_id: str = "local", tenant_id: str = "default") -> dict[str, Any] | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT id, key, value, category, created_at, updated_at FROM memories WHERE key=? AND owner_user_id=? AND tenant_id=?",
            (key, owner_user_id, tenant_id),
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "key": row[1], "value": row[2], "category": row[3], "created_at": row[4], "updated_at": row[5]}

    def search(self, query: str, category: str | None = None, limit: int = 10, owner_user_id: str = "local", tenant_id: str = "default") -> list[dict[str, Any]]:
        conn = self.connect()
        # SQLite 先提供 LIKE 文本检索；语义检索由 Chroma 向量库承担。
        sql = "SELECT id, key, value, category FROM memories WHERE value LIKE ? AND owner_user_id=? AND tenant_id=?"
        params: list[Any] = [f"%{query}%", owner_user_id, tenant_id]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [{"id": r[0], "key": r[1], "value": r[2], "category": r[3]} for r in rows]

    def delete(self, key: str, owner_user_id: str = "local", tenant_id: str = "default") -> bool:
        conn = self.connect()
        cursor = conn.execute(
            "DELETE FROM memories WHERE key=? AND owner_user_id=? AND tenant_id=?",
            (key, owner_user_id, tenant_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def record_session_start(self, session_id: str) -> None:
        conn = self.connect()
        conn.execute("INSERT OR IGNORE INTO sessions (session_id) VALUES (?)", (session_id,))
        conn.commit()

    def record_session_end(self, session_id: str, total_tokens: int) -> None:
        conn = self.connect()
        conn.execute("UPDATE sessions SET ended_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), total_tokens=? WHERE session_id=?", (total_tokens, session_id))
        conn.commit()

    def create_run(
        self,
        run_id: str,
        prompt: str,
        owner_user_id: str,
        tenant_id: str,
        session_id: str,
        status: str = "queued",
        provenance: dict[str, str] | None = None,
    ) -> None:
        conn = self.connect()
        provenance = provenance or {}
        conn.execute(
            """INSERT INTO runs (
                run_id, tenant_id, owner_user_id, session_id, prompt, status,
                agent_version, prompt_version, tool_config_version, policy_version, metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                tenant_id,
                owner_user_id,
                session_id,
                prompt,
                status,
                provenance.get("agent_version"),
                provenance.get("prompt_version"),
                provenance.get("tool_config_version"),
                provenance.get("policy_version"),
                provenance.get("metadata_json"),
            ),
        )
        conn.commit()

    def update_run(
        self,
        run_id: str,
        owner_user_id: str,
        tenant_id: str,
        status: str,
        output: str | None = None,
        confirmation_token: str | None = None,
        error: str | None = None,
        total_tokens: int = 0,
        total_cost: float = 0.0,
    ) -> bool:
        conn = self.connect()
        cursor = conn.execute(
            "UPDATE runs SET status=?, output=?, confirmation_token=?, error=?, total_tokens=COALESCE(total_tokens,0)+?, total_cost_usd=COALESCE(total_cost_usd,0.0)+?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE run_id=? AND owner_user_id=? AND tenant_id=?",
            (status, output, confirmation_token, error, total_tokens, total_cost, run_id, owner_user_id, tenant_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def append_agent_collaboration_event(
        self,
        session_id: str,
        event_type: str,
        from_agent: str,
        to_agent: str,
        task_id: str,
        correlation_id: str,
        payload: dict[str, Any],
    ) -> None:
        conn = self.connect()
        conn.execute(
            """INSERT OR IGNORE INTO agent_collaboration_events
               (session_id, event_type, from_agent, to_agent, task_id, correlation_id, payload_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                event_type,
                from_agent,
                to_agent,
                task_id,
                correlation_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )
        conn.commit()

    def list_agent_collaboration_events(self, session_id: str, correlation_id: str) -> list[dict[str, Any]]:
        rows = self.connect().execute(
            """SELECT event_id, session_id, event_type, from_agent, to_agent, task_id,
                      correlation_id, payload_json, created_at
               FROM agent_collaboration_events
               WHERE session_id=? AND correlation_id=?
               ORDER BY event_id""",
            (session_id, correlation_id),
        ).fetchall()
        return [
            {
                "event_id": row[0],
                "session_id": row[1],
                "event_type": row[2],
                "from_agent": row[3],
                "to_agent": row[4],
                "task_id": row[5],
                "correlation_id": row[6],
                "payload": json.loads(row[7]),
                "created_at": row[8],
            }
            for row in rows
        ]

    def get_run(self, run_id: str, owner_user_id: str, tenant_id: str) -> dict[str, Any] | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT run_id, tenant_id, owner_user_id, session_id, prompt, status, output, confirmation_token, error, created_at, updated_at, attempts, agent_version, prompt_version, tool_config_version, policy_version, metadata_json, total_tokens, total_cost_usd FROM runs WHERE run_id=? AND owner_user_id=? AND tenant_id=?",
            (run_id, owner_user_id, tenant_id),
        ).fetchone()
        if row is None:
            return None
        columns = ["run_id", "tenant_id", "owner_user_id", "session_id", "prompt", "status", "output", "confirmation_token", "error", "created_at", "updated_at", "attempts", "agent_version", "prompt_version", "tool_config_version", "policy_version", "metadata_json", "total_tokens", "total_cost_usd"]
        return dict(zip(columns, row))

    def claim_next_run(self, worker_id: str, lease_seconds: int = 60) -> dict[str, Any] | None:
        conn = self.connect()
        now = datetime.now(UTC)
        # 先回收过期 lease，让崩溃 worker 的任务可重新被认领。
        conn.execute(
            """UPDATE runs SET status='queued', lease_owner=NULL, lease_expires_at=NULL
               WHERE status='running' AND lease_expires_at IS NOT NULL AND datetime(lease_expires_at) < datetime(?)""",
            (now.isoformat(),),
        )
        conn.commit()
        # BEGIN IMMEDIATE 减少多 worker 同时抢同一任务的概率。
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT run_id, tenant_id, owner_user_id, session_id, prompt, attempts FROM runs
               WHERE status='queued' AND (next_retry_at IS NULL OR datetime(next_retry_at) <= datetime(?))
               ORDER BY created_at LIMIT 1""",
            (now.isoformat(),),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        run_id, tenant_id, owner_user_id, session_id, prompt, attempts = row
        expires = now + timedelta(seconds=lease_seconds)
        # 认领即增加 attempts，为重试上限和退避提供依据。
        conn.execute(
            "UPDATE runs SET status='running', lease_owner=?, lease_expires_at=?, attempts=attempts+1 WHERE run_id=?",
            (worker_id, expires.isoformat(), run_id),
        )
        conn.commit()
        return {"run_id": run_id, "tenant_id": tenant_id, "owner_user_id": owner_user_id, "session_id": session_id, "prompt": prompt, "attempts": attempts + 1}

    def renew_run_lease(self, run_id: str, worker_id: str, lease_seconds: int = 60) -> bool:
        conn = self.connect()
        # 只有当前 lease owner 才能续租，防止旧 worker 抢占新 owner。
        expires = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        cursor = conn.execute(
            "UPDATE runs SET lease_expires_at=? WHERE run_id=? AND lease_owner=? AND status='running'",
            (expires.isoformat(), run_id, worker_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def queue_depth(self) -> int:
        return int(self.connect().execute("SELECT count(*) FROM runs WHERE status='queued'").fetchone()[0])

    def running_depth(self) -> int:
        return int(self.connect().execute("SELECT count(*) FROM runs WHERE status='running'").fetchone()[0])

    def dead_letter_depth(self) -> int:
        return int(self.connect().execute("SELECT count(*) FROM runs WHERE status='dead_letter'").fetchone()[0])

    def next_queued_run(self) -> dict[str, Any] | None:
        conn = self.connect()
        row = conn.execute(
            "SELECT run_id, tenant_id, owner_user_id, session_id, prompt FROM runs WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        columns = ["run_id", "tenant_id", "owner_user_id", "session_id", "prompt"]
        return dict(zip(columns, row))
