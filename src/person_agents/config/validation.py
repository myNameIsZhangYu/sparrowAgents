from person_agents.config.settings import Settings


class ConfigurationError(RuntimeError):
    # 聚合所有配置错误后一次性抛出，便于部署期修复。
    pass


def validate_production_settings(config: Settings) -> None:
    errors: list[str] = []
    # 生产必须显式提供鉴权、状态存储、观测和 LLM 配置。
    if not config.api_key_hash:
        errors.append("PERSON_AGENTS_API_KEY_HASH is required")
    if not config.redis_url:
        errors.append("PERSON_AGENTS_REDIS_URL is required")
    if not config.openai_api_key:
        errors.append("OPENAI_API_KEY is required")
    if not config.langfuse_public_key or not config.langfuse_secret_key:
        errors.append("Langfuse keys are required")
    if not config.nextauth_secret:
        errors.append("NEXTAUTH_SECRET is required")

    secrets = [
        config.nextauth_secret,
        config.salt,
        config.encryption_key,
        config.langfuse_secret_key,
    ]
    for name, value in (
        ("NEXTAUTH_SECRET", config.nextauth_secret),
        ("SALT", config.salt),
        ("ENCRYPTION_KEY", config.encryption_key),
    ):
        # 高熵密钥是生产底线，过短值直接拒绝。
        if value and len(value) < 32:
            errors.append(f"{name} must be at least 32 characters")

    values = [value for value in secrets if value]
    if len(values) != len({value for value in values}):
        # 相同密钥会让一个泄露点扩大成多个安全域失守。
        errors.append("Production secrets must be unique")
    if any(value in {"langfuse", "minioadmin", "changeme"} for value in secrets):
        # 显式禁止常见默认密码进入生产。
        errors.append("Default production secrets are forbidden")

    if errors:
        raise ConfigurationError("; ".join(errors))
