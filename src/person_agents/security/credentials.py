import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from person_agents.config.settings import settings
from person_agents.governance.audit import AuditLogger


@dataclass
class CredentialRecord:
    # 数据库中只保存 key_hash；明文只在创建/轮转响应中出现一次。
    key_id: str
    key_hash: str
    tenant_id: str
    user_id: str
    status: str


class CredentialManager:
    def __init__(self, db_path: Path | None = None, audit: AuditLogger | None = None):
        self.db_path = db_path or settings.sqlite_path
        self.audit = audit or AuditLogger(self.db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            # WAL 便于 API 和管理操作并发访问凭证表。
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS credentials (
                    key_id TEXT PRIMARY KEY,
                    key_hash TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    rotated_at TEXT
                )"""
            )
        return self._conn

    @staticmethod
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def create(self, tenant_id: str, user_id: str) -> tuple[str, str]:
        # 明文 key 高熵；key_id 用于日志、审计和后续轮转。
        plaintext = "pa_" + secrets.token_urlsafe(32)
        key_id = secrets.token_hex(12)
        self.connect().execute(
            "INSERT INTO credentials (key_id, key_hash, tenant_id, user_id, status, created_at) VALUES (?,?,?,?,?,?)",
            (key_id, self.digest(plaintext), tenant_id, user_id, "active", datetime.now(UTC).isoformat()),
        )
        self._conn.commit()
        # 记录 key_id 而非明文，满足可审计性。
        self.audit.record("security", "credential_created", key_id, status="success", tenant_id=tenant_id)
        return key_id, plaintext

    def verify(self, plaintext: str) -> CredentialRecord | None:
        # 遍历 active key，并使用 compare_digest 防止时序比较差异。
        rows = self.connect().execute("SELECT key_id,key_hash,tenant_id,user_id,status FROM credentials WHERE status='active'").fetchall()
        candidate = self.digest(plaintext)
        for row in rows:
            if hmac.compare_digest(row[1], candidate):
                return CredentialRecord(*row)
        return None

    def revoke(self, key_id: str) -> bool:
        # 只允许撤销 active key，重复撤销会返回 not_found。
        cursor = self.connect().execute("UPDATE credentials SET status='revoked' WHERE key_id=? AND status='active'", (key_id,))
        self._conn.commit()
        changed = cursor.rowcount > 0
        self.audit.record("security", "credential_revoked", key_id, status="success" if changed else "not_found")
        return changed

    def rotate(self, key_id: str) -> tuple[str, str] | None:
        # 轮转是先撤销旧 key 再创建新 key；生产可增加 grace period。
        row = self.connect().execute("SELECT tenant_id,user_id FROM credentials WHERE key_id=? AND status='active'", (key_id,)).fetchone()
        if row is None:
            return None
        self.revoke(key_id)
        new_id, plaintext = self.create(row[0], row[1])
        self.audit.record("security", "credential_rotated", f"{key_id}->{new_id}", status="success")
        return new_id, plaintext
