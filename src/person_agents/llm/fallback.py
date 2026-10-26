from pathlib import Path
from typing import Any

import httpx
import structlog
import yaml
from openai import OpenAIError

from person_agents.config.settings import settings
from person_agents.llm.client import LLMResponse, call_llm
from person_agents.runtime.llm_settings import latest_llm_settings

logger = structlog.get_logger()


class LLMFallbackError(Exception):
    # 所有 fallback 均失败时抛出；API 层应转换为 503。
    pass


def load_fallback_chain(path: Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        path = Path(__file__).parent / ".." / "config" / "fallback.yaml"
    # YAML 是链路模板；运行时把 settings 中真实模型与 endpoint 注入进去。
    with open(path) as f:
        data = yaml.safe_load(f)
    chain = data.get("fallback_chain", [])
    for entry in chain:
        if entry.get("provider") == "openai":
            if "model" not in entry:
                entry["model"] = settings.openai_model
            entry["base_url"] = settings.openai_base_url
        elif entry.get("provider") == "ollama":
            if "model" not in entry:
                entry["model"] = settings.ollama_model
            entry["base_url"] = settings.ollama_base_url
    return chain


async def call_with_fallback(messages: list[dict[str, str]]) -> LLMResponse:
    chain = load_fallback_chain()
    runtime = latest_llm_settings()
    if runtime:
        for entry in chain:
            if entry.get("provider") == "openai":
                entry["base_url"] = runtime.openai_base_url
                entry["model"] = runtime.openai_model
    last_error: Exception | None = None
    for entry in chain:
        provider = entry.get("provider", "openai")
        if provider != "ollama" and not (runtime or settings.openai_api_key):
            # 没有 API key 时跳过云/兼容 provider，避免必然失败。
            logger.info("skipping_provider_no_key", provider=provider)
            continue
        if provider == "ollama":
            try:
                # 本地模型先做健康探测，减少主请求超时等待。
                async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                    resp = await client.get(settings.ollama_base_url.rstrip("/") + "/api/tags")
                if resp.status_code != 200:
                    logger.info("skipping_ollama_unhealthy", status=resp.status_code)
                    continue
            except httpx.HTTPError:
                logger.info("skipping_ollama_unreachable")
                continue
        model = entry.get("model", "")
        timeout_s = entry.get("timeout_s", 30)
        max_retries = entry.get("max_retries", 1)
        for attempt in range(max_retries):
            try:
                # primary 第一次成功不算 degraded；fallback 或重试成功都标记降级。
                base_url = entry.get("base_url")
                resp = await call_llm(provider, model, messages, timeout_s, base_url)
                resp.degraded = attempt > 0 or entry.get("name") != "primary"
                if resp.degraded:
                    logger.warning("llm_degraded", model=model, provider=provider)
                return resp
            except (httpx.HTTPError, ConnectionError, TimeoutError, OpenAIError) as exc:
                last_error = exc
                # 单次失败继续尝试，链条最后统一抛出。
                logger.warning("llm_attempt_failed", model=model, provider=provider, attempt=attempt, error=str(exc))
    # 保留最后一个异常作为根因，便于外层日志和客户端健康判断。
    raise LLMFallbackError(f"All models in fallback chain failed: {last_error}") from last_error


async def llm_is_available() -> bool:
    chain = load_fallback_chain()
    runtime = latest_llm_settings()
    if runtime:
        for entry in chain:
            if entry.get("provider") == "openai":
                entry["base_url"] = runtime.openai_base_url
                entry["model"] = runtime.openai_model
    for entry in chain:
        provider = entry.get("provider", "openai")
        if provider != "ollama" and not (runtime or settings.openai_api_key):
            continue
        if provider == "ollama":
            try:
                async with httpx.AsyncClient(timeout=2) as client:
                    resp = await client.get(settings.ollama_base_url.rstrip("/") + "/api/tags")
                if resp.status_code == 200:
                    return True
            except httpx.HTTPError:
                continue
        else:
            return True
    return False
