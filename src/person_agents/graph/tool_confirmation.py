from typing import Any

from person_agents.graph.engine import run_graph
from person_agents.graph.runtime import GraphServices
from person_agents.middleware import _confirmations, governed_tool_call
from person_agents.security.context import RequestContext


async def confirm_pending_tool(
    confirmation_token: str,
    *,
    context: RequestContext,
    registry: Any,
    services: GraphServices | None = None,
    resume: bool = False,
    session_id: str | None = None,
) -> Any:

    confirmation = _confirmations.get(confirmation_token)
    if confirmation is None:
        raise PermissionError("Invalid or expired confirmation")
    if confirmation.owner_user_id != context.user_id or confirmation.tenant_id != context.tenant_id:
        raise PermissionError("Confirmation does not match request")

    tool_def = registry.get(confirmation.tool_name)
    if tool_def is None:
        raise PermissionError(f"Unknown tool: {confirmation.tool_name}")

    result = await governed_tool_call(
        confirmation.tool_name,
        confirmation.arguments,
        tool_def.handler,
        context=context,
        tool_schema=tool_def.parameters,
        requires_confirmation=tool_def.requires_confirm,
        confirmation_token=confirmation_token,
    )
    if not resume:
        return result

    resolved_services = services or GraphServices.default()
    resumed = await run_graph(
        "继续",
        session_id=session_id or context.session_id,
        context=context,
        services=resolved_services,
    )
    resumed.setdefault("confirmation_result", result)
    return resumed
