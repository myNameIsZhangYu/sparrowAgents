from person_agents.graph.state import AgentState

MAX_RETRIES = 3


def route_after_supervisor(state: AgentState) -> str:
    if state.get("current_agent") == "memory":
        return "memory_write"
    return "execution_agent"


def route_after_critic(state: AgentState) -> str:
    retry_count = state.get("retry_count", 0)
    if state["critic_feedback"] == "retry" and retry_count < MAX_RETRIES:
        return "executor"
    return "memory_write"


def route_after_executor(state: AgentState) -> str:
    if state["current_step"] < len(state.get("plan", [])):
        return "executor"
    return "critic"


def route_after_planner(state: AgentState) -> str:
    plan = state.get("plan", [])
    if plan and plan[state.get("current_step", 0)].get("tool", "none") != "none":
        return "tool_specialist"
    return "execution_agent"


def route_after_tool(state: AgentState) -> str:
    if state.get("current_step", 0) < len(state.get("plan", [])):
        return "executor"
    return "critic"
