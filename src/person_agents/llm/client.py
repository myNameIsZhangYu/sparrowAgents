from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI

from person_agents.config.settings import settings


@dataclass
class LLMResponse:
    # 统一 OpenAI 兼容模型和 Ollama 的响应形状。
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    degraded: bool = False


async def call_openai(model: str, messages: list[dict[str, str]], timeout_s: int, base_url: str | None = None) -> LLMResponse:
    # 每次创建独立 client，避免跨 fallback provider 复用错误 base_url。
    client = AsyncOpenAI(api_key=settings.openai_api_key, base_url=base_url or settings.openai_base_url)
    # 连接超时单独收紧，尽快发现代理不可达。
    timeout = httpx.Timeout(timeout_s, connect=5.0)
    resp = await client.chat.completions.create(model=model, messages=messages, timeout=timeout)  # type: ignore[arg-type]
    choice = resp.choices[0]
    usage = resp.usage
    return LLMResponse(
        content=choice.message.content or "",
        model=model,
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
    )


async def call_ollama(model: str, messages: list[dict[str, str]], timeout_s: int) -> LLMResponse:
    # 使用 Ollama chat API 的非流式接口，便于统一返回内容与 token。
    base_url = settings.ollama_base_url.rstrip("/") + "/api/chat"
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(base_url, json={"model": model, "messages": messages, "stream": False})
        resp.raise_for_status()
        data = resp.json()
    return LLMResponse(
        content=data["message"]["content"],
        model=model,
        prompt_tokens=data.get("prompt_eval_count", 0),
        completion_tokens=data.get("eval_count", 0),
    )


async def call_llm(provider: str, model: str, messages: list[dict[str, str]], timeout_s: int, base_url: str | None = None) -> LLMResponse:
    # 路由入口：目前只有 ollama 特殊路径，其余都按 OpenAI 兼容协议处理。
    if provider == "ollama":
        return await call_ollama(model, messages, timeout_s)
    return await call_openai(model, messages, timeout_s, base_url)
