import hashlib
import hmac
import secrets
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from person_agents.config.settings import settings
from person_agents.governance.audit_store import AuditStore, create_audit_store


@dataclass
class CredentialRecord:
    # PostgreSQL 凭证记录与 SQLite 版本保持同一形状。
    key_id: str
    key_hash: str
    tenant_id: str
    user_id: str
    status: str


class PostgresCredentialStore:
    SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS credentials (
        key_id TEXT PRIMARY KEY,
        key_hash TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        rotated_at TIMESTAMPTZ
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_credentials_tenant_user
        ON credentials(tenant_id, user_id, status);
    """,
    )

    def __init__(self, dsn: str | None = None, audit: AuditStore | None = None):
        # 凭证访问记录到独立审计后端，便于安全事件追踪。
        self._dsn = dsn or settings.postgres_dsn
        self.engine = create_async_engine(self._dsn)
        self.audit = audit or create_audit_store()

    async def bootstrap(self) -> None:
        async with self.engine.begin() as conn:
            # 幂等建表和索引，部署迁移服务会提前调用。
            for statement in self.SCHEMA_STATEMENTS:
                await conn.execute(text(statement))

    @staticmethod
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    async def create(self, tenant_id: str, user_id: str) -> tuple[str, str]:
        # 创建后明文只返回一次；审计只记录 key_id。
        plaintext = "pa_" + secrets.token_urlsafe(32)
        key_id = secrets.token_hex(12)
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """INSERT INTO credentials (key_id,key_hash,tenant_id,user_id,status,created_at)
                       VALUES (:key_id,:key_hash,:tenant_id,:user_id,'active',now())"""
                ),
                {
                    "key_id": key_id,
                    "key_hash": self.digest(plaintext),
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                },
            )
        await self.audit.arecord("security", "credential_created", key_id, tenant_id=tenant_id)
        return key_id, plaintext

    async def verify(self, plaintext: str) -> CredentialRecord | None:
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT key_id,key_hash,tenant_id,user_id,status FROM credentials WHERE status='active'")
                )
            ).all()
        candidate = self.digest(plaintext)
        for row in rows:
            # compare_digest 防止哈希比较的时序侧信道。
            if hmac.compare_digest(row[1], candidate):
                return CredentialRecord(*row)
        return None

    async def revoke(self, key_id: str) -> bool:
        async with self.engine.begin() as conn:
            # 状态条件更新保证撤销语义幂等。
            result = await conn.execute(
                text("UPDATE credentials SET status='revoked' WHERE key_id=:key_id AND status='active'"),
                {"key_id": key_id},
            )
        changed = result.rowcount > 0
        await self.audit.arecord(
            "security", "credential_revoked", key_id, status="success" if changed else "not_found"
        )
        return changed

    async def rotate(self, key_id: str) -> tuple[str, str] | None:
        async with self.engine.connect() as conn:
            # 轮转保持 tenant/user 不变，只替换凭证。
            row = (
                await conn.execute(
                    text("SELECT tenant_id,user_id FROM credentials WHERE key_id=:key_id AND status='active'"),
                    {"key_id": key_id},
                )
            ).first()
        if row is None:
            return None
        await self.revoke(key_id)
        new_id, plaintext = await self.create(row[0], row[1])
        await self.audit.arecord("security", "credential_rotated", f"{key_id}->{new_id}")
        return new_id, plaintext
