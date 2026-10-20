"""Unified middleware for LLM and tool calls.

Every call goes through: rate_limit → policy → audit → trace.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import jsonschema
import structlog

from person_agents.config.settings import settings
from person_agents.governance.audit_store import create_audit_store
from person_agents.governance.policy import Permission, PolicyEngine
from person_agents.governance.rate_limiter import RateLimiter
from person_agents.observability.langfuse import trace_llm_call
from person_agents.observability.metrics import llm_calls_total, tool_calls_total
from person_agents.runtime.state import create_state_store
from person_agents.security.confirmation import ConfirmationStore
from person_agents.security.context import RequestContext

logger = structlog.get_logger()

_policy = PolicyEngine()
_state_store = create_state_store(settings.redis_url)
_rate_limiter = RateLimiter(_state_store)
_audit = create_audit_store()
_confirmations = ConfirmationStore(_state_store)

def _configure_policy() -> None:
    for value in settings.granted_permissions.split(","):
        permission = Permission(value.strip().lower())
        _policy.grant(permission)


_configure_policy()


async def governed_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    handler: Callable[..., Awaitable[Any]],
    context: RequestContext | None = None,
    request_id: str | None = None,
    tool_schema: dict[str, Any] | None = None,
    requires_confirmation: bool = False,
    confirmation_token: str | None = None,
) -> Any:
    context = context or RequestContext(user_id="local", tenant_id="default", session_id="cli")
    request_id = request_id or uuid.uuid4().hex
    owner = context.user_id
    tenant = context.tenant_id
    if not _rate_limiter.allow_tool(owner, tenant):
        await _audit.arecord("middleware", f"tool:{tool_name}", str(arguments), status="rate_limited", request_id=request_id, owner_user_id=owner, tenant_id=tenant)
        raise RuntimeError(f"Tool call rate limited: {tool_name}")

    if tool_schema:
        try:
            jsonschema.validate(arguments, tool_schema)
        except jsonschema.ValidationError as exc:
            await _audit.arecord("middleware", f"tool:{tool_name}", str(arguments), status="invalid_arguments", error=exc.message, request_id=request_id, owner_user_id=owner, tenant_id=tenant)
            raise ValueError(f"Invalid arguments for {tool_name}: {exc.message}") from exc

    required_permission = Permission.SHELL if "sandbox" in tool_name else Permission.WRITE if "write" in str(arguments.get("operation", "")) else Permission.READ
    if not _policy.check(required_permission):
        await _audit.arecord("middleware", f"tool:{tool_name}", str(arguments), status="permission_denied", request_id=request_id, owner_user_id=owner, tenant_id=tenant)
        raise PermissionError(f"Permission denied for {required_permission.value}: {tool_name}")

    if tool_name == "shell":
        command = str(arguments.get("command", ""))
        allowed, reason = _policy.check_command(command)
        if not allowed:
            await _audit.arecord("middleware", f"tool:{tool_name}", command, status="policy_rejected", error=reason, request_id=request_id, owner_user_id=owner, tenant_id=tenant)
            raise PermissionError(reason)

    if requires_confirmation:
        if confirmation_token is None:
            confirmation = _confirmations.create(owner, tenant, tool_name, arguments)
            await _audit.arecord("middleware", f"tool:{tool_name}", str(arguments), status="confirmation_required", request_id=request_id, owner_user_id=owner, tenant_id=tenant)
            return {"status": "confirmation_required", "confirmation_token": confirmation.token}
        confirmation = _confirmations.consume(confirmation_token)
        if confirmation is None:
            await _audit.arecord("middleware", f"tool:{tool_name}", str(arguments), status="invalid_confirmation", request_id=request_id, owner_user_id=owner, tenant_id=tenant)
            raise PermissionError("Invalid or expired confirmation")
        if confirmation.owner_user_id != owner or confirmation.tenant_id != tenant or confirmation.tool_name != tool_name:
            raise PermissionError("Confirmation does not match request")

    result = await handler(arguments)
    await _audit.arecord("middleware", f"tool:{tool_name}", str(arguments), str(result)[:200], request_id=request_id, owner_user_id=owner, tenant_id=tenant)
    tool_calls_total.labels(tool_name, "success").inc()
    return result


async def governed_llm_call(
    messages: list[dict[str, str]],
    call_fn: Callable[..., Awaitable[Any]],
    context: RequestContext | None = None,
) -> Any:
    context = context or RequestContext(user_id="local", tenant_id="default", session_id="cli")
    request_id = uuid.uuid4().hex
    if not _rate_limiter.allow_llm(context.user_id, context.tenant_id):
        await _audit.arecord("middleware", "llm", "rate limited", status="rate_limited", request_id=request_id, owner_user_id=context.user_id, tenant_id=context.tenant_id)
        raise RuntimeError("LLM call rate limited")

    import time
    start = time.monotonic()
    result = await call_fn(messages)
    _rate_limiter.record_llm_usage(result, context.user_id, context.tenant_id)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    llm_calls_total.labels("success", str(result.degraded).lower()).inc()

    await _audit.arecord("middleware", "llm", str(messages)[-200:], str(result.content)[:200], "success")
    trace_llm_call(
        "agent-llm",
        result.model,
        result.prompt_tokens,
        result.completion_tokens,
        elapsed_ms,
        messages=messages,
        output=result.content,
        degraded=result.degraded,
    )
    return result
