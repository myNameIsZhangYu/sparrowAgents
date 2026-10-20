from person_agents.config.settings import settings
from person_agents.governance.audit_store import PostgresAuditStore
from person_agents.memory.postgres_store import PostgresStore
from person_agents.runtime.config_repository import PostgresConfigRegistry
from person_agents.security.credential_store import PostgresCredentialStore


async def wait_for_migrations(dsn: str | None = None) -> None:
    dsn = dsn or settings.postgres_dsn
    if not dsn:
        return
    # 启动前确认核心、审计、凭证、配置四类 schema 都已初始化。
    await PostgresStore(dsn).bootstrap()
    await PostgresAuditStore(dsn).record("startup", "migration_check", "bootstrap")
    await PostgresCredentialStore(dsn).bootstrap()
    await PostgresConfigRegistry(dsn).bootstrap()
