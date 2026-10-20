
import time
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from person_agents.config.settings import settings
from person_agents.governance.policy import PolicyEngine
from person_agents.graph.edges import (
    route_after_critic,
    route_after_executor,
    route_after_planner,
    route_after_supervisor,
    route_after_tool,
)
from person_agents.graph.nodes import (
    critic_node,
    executor_node,
    intent_node,
    memory_write_node,
    planner_node,
)
from person_agents.graph.state import AgentState
from person_agents.middleware import governed_tool_call
from person_agents.observability.metrics import agent_run_duration_seconds, agent_runs_total
from person_agents.security.context import RequestContext

GREETING_REPLIES = {
    "hello": "Hello! How can I help you today?",
    "hi": "Hi! How can I help you today?",
    "hey": "Hey! How can I help you today?",
}


def _fast_reply(user_input: str) -> str | None:
    # 规范化常见标点和大小写，让打招呼命中快速回复。
    normalized = user_input.strip().lower().rstrip("!?., ")
    if normalized in GREETING_REPLIES:
        return GREETING_REPLIES[normalized]
    # 支持 “Hi there” 这类常见寒暄开头。
    for greeting in GREETING_REPLIES:
        if normalized == greeting or normalized.startswith(greeting + " "):
            return GREETING_REPLIES["hello"]
    return None


async def supervisor_node(state: AgentState) -> dict[str, Any]:
    started = time.monotonic()
    # 根据意图决定后续是记忆写流程还是执行编排。
    specialist = "memory" if state["intent"] == "memory_write" else "executor"
    result = {"current_agent": specialist, "agent_history": ["supervisor"]}
    agent_runs_total.labels(agent="supervisor", status="success").inc()
    agent_run_duration_seconds.labels(agent="supervisor").observe(time.monotonic() - started)
    return result


async def execution_specialist(state: AgentState) -> dict[str, Any]:
    started = time.monotonic()
    history = state.get("agent_history", [])
    # 防止重复进入 executor 时把同一个 agent 重复记录进历史。
    result = {"current_agent": "executor", "agent_history": [*history, "executor"] if history and history[-1] != "executor" else history}
    agent_runs_total.labels(agent="executor", status="success").inc()
    agent_run_duration_seconds.labels(agent="executor").observe(time.monotonic() - started)
    return result


async def tool_specialist(state: AgentState, registry: Any | None = None) -> dict[str, Any]:
    started = time.monotonic()
    resolved_registry = registry
    if resolved_registry is None:
        # 未显式注入时回退到模块级默认工具注册表。
        from person_agents.graph.nodes import _get_registry

        resolved_registry = _get_registry()
    step = state["plan"][state["current_step"]]
    tool = step.get("tool", "none")
    tool_def = resolved_registry.get(tool)
    # 从图状态重建工具调用的租户安全上下文。
    context = RequestContext(
        user_id=state.get("owner_user_id", state.get("session_id", "local")),
        tenant_id=state.get("tenant_id", "default"),
        session_id=state.get("session_id", "cli"),
    )
    if not tool_def:
        # 未知工具必须降级，而不是静默执行或抛出未处理异常。
        output = f"Unknown tool: {tool}"
        degraded = True
        error = output
    else:
        arguments = step.get("arguments") or {}
        # 所有工具调用都经过限流、权限、schema、确认、审计中间件。
        tool_result = await governed_tool_call(
            tool,
            arguments,
            tool_def.handler,
            context=context,
            tool_schema=tool_def.parameters,
            requires_confirmation=tool_def.requires_confirm,
        )
        output = tool_result if isinstance(tool_result, dict) and tool_result.get("status") == "confirmation_required" else str(tool_result)
        if not isinstance(output, dict):
            # 输出内容也要过治理，防止敏感信息回传给调用方。
            output_allowed, governed_output = PolicyEngine().sanitize_tool_output(output)
            if not output_allowed:
                output = governed_output
                degraded = True
                error = governed_output
        if "degraded" not in locals():
            # 只有确认请求和未降级路径会进入这里。
            degraded = False
            error = None
    history = state.get("agent_history", [])
    # 连续工具调用只记录一次 tool 身份，保持 agent_history 可读。
    history = [*history, "tool"] if not history or history[-1] != "tool" else history
    result = {
        "tool_results": [*state.get("tool_results", []), {"step": state["current_step"], "action": step.get("action", ""), "tool": tool, "output": output}],
        "current_step": state["current_step"] + 1,
        "current_agent": "tool",
        "agent_history": history,
        "degraded": degraded,
        "status": "failed" if degraded else state.get("status", ""),
        "error": error,
    }
    agent_runs_total.labels(agent="tool", status="failed" if degraded else "success").inc()
    agent_run_duration_seconds.labels(agent="tool").observe(time.monotonic() - started)
    return result


def _observe_agent(agent: str, started: float, status: str = "success") -> None:
    # 统一节点生命周期和延迟观测。
    agent_runs_total.labels(agent=agent, status=status).inc()
    agent_run_duration_seconds.labels(agent=agent).observe(time.monotonic() - started)


def build_graph(checkpointer: AsyncSqliteSaver | None = None, services: Any | None = None):
    builder = StateGraph(AgentState)
    async def tool_node(state: AgentState) -> dict[str, Any]:
        # 闭包捕获注入的 registry，避免使用模块全局状态。
        return await tool_specialist(state, registry=services.registry if services else None)

    builder.add_node("tool_specialist", tool_node)
    builder.add_conditional_edges(
        "planner",
        route_after_planner,
        # 注意：router 返回 execution_agent 时，实际执行节点是 executor。
        {"tool_specialist": "tool_specialist", "execution_agent": "executor"},
    )
    builder.add_conditional_edges(
        "tool_specialist",
        route_after_tool,
        {"critic": "critic"},
    )
    if services is not None:
        # 注入服务是生产 API / worker 的主路径，保证依赖可替换、可测试。
        async def injected_intent(state: AgentState) -> dict[str, Any]:
            return await intent_node(state, vector=services.vector)

        async def injected_planner(state: AgentState) -> dict[str, Any]:
            return await planner_node(state, registry=services.registry)

        async def injected_memory(state: AgentState) -> dict[str, Any]:
            return await memory_write_node(state, store=services.store, vector=services.vector)

        builder.add_node("intent", injected_intent)
        builder.add_node("planner", injected_planner)
        builder.add_node("memory_write", injected_memory)
    else:
        # CLI 或测试可回退到默认单例依赖。
        builder.add_node("intent", intent_node)
        builder.add_node("planner", planner_node)
        builder.add_node("memory_write", memory_write_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("execution_agent", execution_specialist)
    builder.add_node("executor", executor_node)
    builder.add_node("critic", critic_node)

    builder.add_edge(START, "intent")
    builder.add_edge("intent", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"execution_agent": "execution_agent", "memory_write": "memory_write"},
    )
    builder.add_edge("execution_agent", "planner")
    builder.add_conditional_edges("executor", route_after_executor, {"executor": "executor", "critic": "critic"})
    builder.add_conditional_edges("critic", route_after_critic, {"executor": "executor", "memory_write": "memory_write"})
    builder.add_edge("memory_write", END)

    if checkpointer is None:
        # 默认使用 SQLite checkpoint，便于单机部署；生产可显式传入其他 checkpointer。
        import aiosqlite
        conn = aiosqlite.connect(str(settings.sqlite_path))
        checkpointer = AsyncSqliteSaver(conn)

    return builder.compile(checkpointer=checkpointer)


async def run_graph(
    user_input: str,
    session_id: str = "cli",
    context: RequestContext | None = None,
    services: Any | None = None,
) -> AgentState:
    context = context or RequestContext(user_id=session_id, tenant_id="default", session_id=session_id)
    # 先构造完整初始状态，快速回复和普通运行共享同一字段契约。
    initial_state: AgentState = {
        "messages": [{"role": "user", "content": user_input}],
        "intent": "",
        "plan": [],
        "current_step": 0,
        "tool_results": [],
        "critic_feedback": "",
        "memory_context": [],
        "status": "",
        "degraded": False,
        "error": None,
        "retry_count": 0,
        "session_id": context.session_id,
        "tenant_id": context.tenant_id,
        "owner_user_id": context.user_id,
        "current_agent": "",
        "agent_history": [],
        # loop guard 依赖该时间戳自检是否已运行过久。
        "started_at": time.monotonic(),
    }

    # 打招呼等低风险请求直接返回，节省 LLM 成本和延迟。
    reply = _fast_reply(user_input)
    if reply is not None:
        return {
            **initial_state,
            "intent": "chat",
            "current_agent": "supervisor",
            "tool_results": [{"step": 0, "action": "greeting", "tool": "none", "output": reply}],
            "status": "completed",
            "degraded": False,
        }

    graph = build_graph(services=services)
    # session_id 用作 checkpoint thread，维持同一会话状态。
    config = {"configurable": {"thread_id": session_id}}
    result = await graph.ainvoke(initial_state, config)
    return result
