from typing import Any

import structlog

from person_agents.config.settings import settings

logger = structlog.get_logger()

_client = None


def get_langfuse():
    global _client
    if _client is None and settings.langfuse_public_key and settings.langfuse_secret_key:
        from langfuse import Langfuse
        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return _client


def _propagate_attributes(**kwargs):
    from langfuse import propagate_attributes

    return propagate_attributes(**kwargs)


def trace_graph_node(
    session_id: str,
    node_name: str,
    input_data: Any,
    output_data: Any,
    latency_ms: int,
    error: str | None = None,
) -> None:
    client = get_langfuse()
    if client is None:
        return
    try:
        with _propagate_attributes(session_id=session_id, trace_name="agent_graph"), \
            client.start_as_current_observation(name="agent_graph"), \
            client.start_as_current_observation(name=f"graph_node:{node_name}") as span:
            span.update(
                input=input_data,
                output=output_data,
                metadata={"latency_ms": latency_ms, "error": error},
                level="ERROR" if error else "DEFAULT",
            )
        client.flush()
    except Exception:
        logger.warning("langfuse_trace_failed", node=node_name, exc_info=True)


def trace_llm_call(
    session_id: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    messages: list[dict[str, str]] | None = None,
    output: str | None = None,
    degraded: bool = False,
    error: str | None = None,
) -> None:
    client = get_langfuse()
    if client is None:
        return
    try:
        with client.start_as_current_observation(
            name="llm_call",
        ) as obs:
            obs.update(
                input=messages or {},
                output={"content": output or ""},
                metadata={
                    "model": model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "latency_ms": latency_ms,
                    "degraded": degraded,
                    "session_id": session_id,
                },
            )
        client.flush()
        if error:
            logger.warning("langfuse_error", error=error)
    except Exception:
        logger.warning("langfuse_trace_failed", exc_info=True)
