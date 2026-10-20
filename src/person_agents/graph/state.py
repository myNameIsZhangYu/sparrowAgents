from operator import add
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict):
    # 用户消息使用 reducer 累加，避免多节点返回时互相覆盖对话历史。
    messages: Annotated[list[dict[str, str]], add]
    intent: str
    plan: list[dict[str, Any]]
    current_step: int
    # 工具结果同样跨节点累加，保留每一步的执行证据。
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
    # 记录首次进入图的时间，用于 critic 的长时间循环自检。
    started_at: float | None
