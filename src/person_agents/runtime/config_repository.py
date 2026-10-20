import hashlib
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from person_agents.config.settings import settings


@dataclass
class ConfigVersion:
    kind: str
    version_id: str
    content: str


class PostgresConfigRegistry:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS config_versions (
        kind TEXT NOT NULL,
        version_id TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (kind, version_id)
    );
    """

    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or settings.postgres_dsn
        self.engine = create_async_engine(self._dsn)

    async def bootstrap(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text(self.SCHEMA))

    @staticmethod
    def version_id(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:24]

    async def register(self, kind: str, content: str) -> ConfigVersion:
        version_id = self.version_id(content)
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """INSERT INTO config_versions (kind,version_id,content) VALUES (:kind,:version_id,:content)
                       ON CONFLICT (kind,version_id) DO NOTHING"""
                ),
                {"kind": kind, "version_id": version_id, "content": content},
            )
        return ConfigVersion(kind, version_id, content)

    async def get(self, kind: str, version_id: str) -> ConfigVersion | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT kind,version_id,content FROM config_versions WHERE kind=:kind AND version_id=:version_id"),
                    {"kind": kind, "version_id": version_id},
                )
            ).first()
        return ConfigVersion(*row) if row else None

    async def latest(self, kind: str) -> ConfigVersion | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """SELECT kind,version_id,content FROM config_versions WHERE kind=:kind
                           ORDER BY created_at DESC, version_id LIMIT 1"""
                    ),
                    {"kind": kind},
                )
            ).first()
        return ConfigVersion(*row) if row else None
