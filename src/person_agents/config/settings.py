from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # pydantic-settings 从环境变量和 local/env/app.env 读取；类型校验在实例化时执行。
    model_config = SettingsConfigDict(env_file="local/env/app.env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "person-agents"
    debug: bool = False
    testing: bool = False

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"

    log_level: str = "INFO"
    log_format: str = "console"

    data_dir: Path = Field(
        default=Path.home() / ".person-agents",
        validation_alias=AliasChoices("PERSON_AGENTS_DATA_DIR", "DATA_DIR"),
    )
    sqlite_path: Path = Field(
        default=Path.home() / ".person-agents" / "memory.db",
        validation_alias=AliasChoices("PERSON_AGENTS_SQLITE_PATH", "SQLITE_PATH"),
    )
    audit_db_path: Path = Field(
        default=Path.home() / ".person-agents" / "audit.db",
        validation_alias=AliasChoices("PERSON_AGENTS_AUDIT_DB_PATH", "AUDIT_DB_PATH"),
    )
    state_db_path: Path = Field(
        default=Path.home() / ".person-agents" / "state.db",
        validation_alias=AliasChoices("PERSON_AGENTS_STATE_DB_PATH", "STATE_DB_PATH"),
    )
    chroma_path: Path = Field(
        default=Path.home() / ".person-agents" / "chroma",
        validation_alias=AliasChoices("PERSON_AGENTS_CHROMA_PATH", "CHROMA_PATH"),
    )
    chroma_model_cache_path: Path = Field(
        default=Path.home() / ".cache" / "chroma" / "onnx_models" / "all-MiniLM-L6-v2",
        validation_alias=AliasChoices("PERSON_AGENTS_CHROMA_MODEL_CACHE_PATH", "CHROMA_MODEL_CACHE_PATH"),
    )

    langfuse_host: str = "http://localhost:3000"
    langfuse_enabled: bool = True
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    sandbox_image: str = "person-agents/sandbox:latest"
    sandbox_timeout_s: int = 30
    sandbox_memory_mb: int = 256
    sandbox_cpus: float = 0.5
    granted_permissions: str = "read,write"
    api_key_hash: str = ""
    default_tenant_id: str = "default"
    redis_url: str = ""
    postgres_dsn: str = ""
    postgres_pool_size: int = 5
    postgres_max_overflow: int = 10
    admin_api_key_hash: str = ""
    tenant_max_running_runs: int = 3
    tenant_daily_run_limit: int = 1000
    tenant_daily_token_limit: int = 1_000_000
    tenant_daily_cost_limit: float = 10.0
    run_max_attempts: int = 3
    run_retry_backoff_seconds: int = 30
    run_timeout_s: int = 120
    loop_guard_seconds: float = 15.0
    llm_timeout_s: int = 30
    vector_timeout_s: float = 5
    agent_version: str = "0.1.0"
    prompt_version: str = "default-v1"
    tool_config_version: str = "default-v1"
    policy_version: str = "default-v1"
    nextauth_secret: str = ""
    salt: str = ""
    encryption_key: str = ""
    # 审计保留天数由部署方按合规要求配置。
    audit_retention_days: int = 90
    # 运行时 LLM 覆盖配置的加密密钥；生产必须设置高熵密钥。
    runtime_secret: str = ""


settings = Settings()
