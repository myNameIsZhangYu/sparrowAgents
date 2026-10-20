from operator import add
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict):
    messages: Annotated[list[dict[str, str]], add]
    intent: str
    plan: list[dict[str, Any]]
    current_step: int
    tool_results: Annotated[list[dict[str, Any]], add]
    critic_feedback: str
    memory_context: list[dict[str, Any]]
    status: str
    degraded: bool
    error: str | None
    retry_count: int
    session_id: str
    tenant_id: str
    owner_user_id: str
    current_agent: str
    agent_history: list[str]
    started_at: float | None
