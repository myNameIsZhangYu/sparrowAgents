# 图路由规则集中在这里，确保节点返回值与 LangGraph edge 映射保持一致。
from person_agents.graph.state import AgentState

MAX_RETRIES = 3


def route_after_supervisor(state: AgentState) -> str:
    # supervisor 只负责拆分：记忆写任务进入 memory_write，其他任务进入执行编排。
    if state.get("current_agent") == "memory":
        return "memory_write"
    return "execution_agent"


def route_after_critic(state: AgentState) -> str:
    # 仅在 critic 明确要求 retry 且未超过次数上限时继续重试。
    retry_count = state.get("retry_count", 0)
    if state["critic_feedback"] == "retry" and retry_count < MAX_RETRIES:
        return "executor"
    return "memory_write"


def route_after_executor(state: AgentState) -> str:
    # 计划步骤未执行完时继续 executor，否则交给 critic 评估。
    if state["current_step"] < len(state.get("plan", [])):
        return "executor"
    return "critic"


def route_after_planner(state: AgentState) -> str:
    # 当前步骤需要工具时进入受治理的 tool_specialist；普通文本任务直接进入 executor。
    plan = state.get("plan", [])
    if plan and plan[state.get("current_step", 0)].get("tool", "none") != "none":
        return "tool_specialist"
    return "execution_agent"


def route_after_tool(state: AgentState) -> str:
    # 工具调用推进了 current_step，仍剩余步骤时回 executor。
    if state.get("current_step", 0) < len(state.get("plan", [])):
        return "executor"
    return "critic"
