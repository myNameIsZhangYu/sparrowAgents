from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "person-agents"
    debug: bool = False

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"

    log_level: str = "INFO"
    log_format: str = "console"

    data_dir: Path = Path.home() / ".person-agents"
    sqlite_path: Path = Path.home() / ".person-agents" / "memory.db"
    chroma_path: Path = Path.home() / ".person-agents" / "chroma"

    langfuse_host: str = "http://localhost:3000"
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
    audit_retention_days: int = 90


settings = Settings()
