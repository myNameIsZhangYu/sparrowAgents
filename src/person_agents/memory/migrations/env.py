from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from person_agents.config.settings import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
    # 迁移目标始终指向 settings 中的 SQLite 数据库。
config.set_main_option("sqlalchemy.url", f"sqlite:///{settings.sqlite_path}")
target_metadata = None


def run_migrations_offline() -> None:
    # 生成 SQL 时不需要真实连接。
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # 在线模式用独立连接执行 Alembic 迁移。
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
