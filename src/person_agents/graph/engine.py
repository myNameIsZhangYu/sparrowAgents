
import asyncio
import time
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from person_agents.config.settings import settings
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
    run_tool_step,
)
from person_agents.graph.state import AgentState
from person_agents.graph.task_graph import (
    AgentCapabilityRegistry,
    normalize_plan_to_task_graph,
)
from person_agents.llm.fallback import llm_is_available
from person_agents.observability.metrics import agent_run_duration_seconds, agent_runs_total
from person_agents.security.context import RequestContext

GREETING_REPLIES = {
    "hello": "Hello! How can I help you today?",
    "hi": "Hi! How can I help you today?",
    "hey": "Hey! How can I help you today?",
    "你好": "你好！我可以协助你运行任务、操作工具或查询信息。",
    "您好": "您好！我可以协助你运行任务、操作工具或查询信息。",
    "hi there": "Hi! How can I help you today?",
    "hello there": "Hello! How can I help you today?",
}

agent_capabilities = AgentCapabilityRegistry()
agent_capabilities.register(
    "executor",
    task_types=["llm"],
    allowed_tools=["none"],
    max_concurrency=4,
)
agent_capabilities.register(
    "tool",
    task_types=["tool_use"],
    allowed_tools=["filesystem", "shell"],
    max_concurrency=2,
)


def _fast_reply(user_input: str) -> str | None:
    # 规范化常见标点和大小写，让打招呼命中快速回复。
    normalized = user_input.strip().lower().rstrip("!?.,，！？。 ")
    if normalized in GREETING_REPLIES:
        return GREETING_REPLIES[normalized]
    # 支持 “Hi there” 这类常见寒暄开头。
    for greeting in ("hello", "hi", "hey"):
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
    if specialist == "executor":
        prompt = state.get("messages", [{}])[-1].get("content", "")
        subtasks = normalize_plan_to_task_graph(state.get("plan", [{"action": prompt, "tool": "none"}]), prompt)
        for task in subtasks:
            selected = agent_capabilities.select(
                "tool_use" if task["tool"] != "none" else "llm",
                allowed_tools=[task["tool"]],
            )
            if selected:
                task["agent"] = selected
        return {
            **result,
            "subtasks": subtasks,
            "pending_subtask_ids": [task["id"] for task in subtasks],
            "subtask_results": [],
            "delegation_method": "capability_registry",
        }
    return {**result, "subtasks": [], "pending_subtask_ids": [], "subtask_results": []}


async def execution_specialist(state: AgentState) -> dict[str, Any]:
    started = time.monotonic()
    history = state.get("agent_history", [])
    # 防止重复进入 executor 时把同一个 agent 重复记录进历史。
    result = {"current_agent": "executor", "agent_history": [*history, "executor"] if history and history[-1] != "executor" else history}
    agent_runs_total.labels(agent="executor", status="success").inc()
    agent_run_duration_seconds.labels(agent="executor").observe(time.monotonic() - started)
    return result


async def tool_specialist(state: AgentState, registry: Any | None = None, services: Any | None = None) -> dict[str, Any]:
    started = time.monotonic()
    resolved_registry = registry
    if resolved_registry is None:
        # 未显式注入时回退到模块级默认工具注册表。
        from person_agents.graph.nodes import _get_registry

        resolved_registry = _get_registry()
    step = state["plan"][state["current_step"]]
    tool = step.get("tool", "none")
    tool_result = await run_tool_step(step, state, resolved_registry, services=services)
    output = tool_result["output"]
    degraded = tool_result["degraded"]
    error = tool_result["error"]
    history = state.get("agent_history", [])
    # 连续工具调用只记录一次 tool 身份，保持 agent_history 可读。
    history = [*history, "tool"] if not history or history[-1] != "tool" else history
    if isinstance(output, dict) and output.get("status") == "confirmation_required":
        confirmation_output = {
            **output,
            "tool": tool,
            "arguments": step.get("arguments") or {},
        }
        return {
            "tool_results": [*state.get("tool_results", []), {"step": state["current_step"], "action": step.get("action", ""), "tool": tool, "output": confirmation_output}],
            "current_step": state["current_step"] + 1,
            "current_agent": "tool",
            "agent_history": history,
            "status": "confirmation_required",
            "pending_confirmation": confirmation_output["confirmation_token"],
        }
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
    # LangGraph 同步条件路由会经过默认线程执行器；取消后该池可能阻塞解释器退出。
    # 这里统一包装为异步路由，保持 CLI/测试的生命周期可关闭。
    async def route_after_planner_async(state: AgentState) -> str:
        return route_after_planner(state)

    async def route_after_tool_async(state: AgentState) -> str:
        return route_after_tool(state)

    async def route_after_supervisor_async(state: AgentState) -> str:
        return route_after_supervisor(state)

    async def route_after_executor_async(state: AgentState) -> str:
        return route_after_executor(state)

    async def route_after_critic_async(state: AgentState) -> str:
        return route_after_critic(state)

    async def tool_node(state: AgentState) -> dict[str, Any]:
        # 闭包捕获注入的 registry，避免使用模块全局状态。
        return await tool_specialist(state, registry=services.registry if services else None, services=services)

    builder.add_node("tool_specialist", tool_node)
    builder.add_conditional_edges(
        "planner",
        route_after_planner_async,
        # 注意：router 返回 execution_agent 时，实际执行节点是 executor。
        {"tool_specialist": "tool_specialist", "execution_agent": "executor"},
    )
    builder.add_conditional_edges(
        "tool_specialist",
        route_after_tool_async,
        {"executor": "executor", "critic": "critic", END: END},
    )
    if services is not None:
        # 注入服务是生产 API / worker 的主路径，保证依赖可替换、可测试。
        async def injected_intent(state: AgentState) -> dict[str, Any]:
            return await intent_node(state, vector=services.vector)

        async def injected_planner(state: AgentState) -> dict[str, Any]:
            return await planner_node(state, registry=services.registry, services=services)

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
    async def executor_with_services(state: AgentState) -> dict[str, Any]:
        return await executor_node(state, services=services)

    builder.add_node("executor", executor_with_services)
    builder.add_node("critic", critic_node)

    builder.add_edge(START, "intent")
    builder.add_edge("intent", "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor_async,
        {"execution_agent": "execution_agent", "memory_write": "memory_write"},
    )
    builder.add_edge("execution_agent", "planner")
    builder.add_conditional_edges("executor", route_after_executor_async, {"executor": "executor", "critic": "critic"})
    builder.add_conditional_edges("critic", route_after_critic_async, {"executor": "executor", "memory_write": "memory_write"})
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
    summary = ""
    if services is not None and hasattr(services.store, "get_session_summary"):
        summary = services.store.get_session_summary(
            session_id,
            owner_user_id=context.user_id,
            tenant_id=context.tenant_id,
        ) or ""
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
        "pending_confirmation": None,
        # loop guard 依赖该时间戳自检是否已运行过久。
        "started_at": time.monotonic(),
        "subtasks": [],
        "pending_subtask_ids": [],
        "subtask_results": [],
        "session_summary": summary,
    }

    # 打招呼等低风险请求直接返回，节省 LLM 成本和延迟。
    reply = _fast_reply(user_input)
    if reply is not None:
        return {
            **initial_state,
            "intent": "chat",
            "current_agent": "supervisor",
            "tool_results": [{"step": 0, "action": "greeting", "tool": "none", "output": reply}],
            "pending_confirmation": None,
            "status": "completed",
            "degraded": False,
                "subtasks": [],
                "pending_subtask_ids": [],
                "subtask_results": [],
        }
    if not await llm_is_available():
        return {
            **initial_state,
            "intent": "task",
            "current_agent": "supervisor",
            "status": "failed",
            "degraded": True,
            "error": "No LLM provider is available",
            "tool_results": [{"step": 0, "action": "llm_unavailable", "tool": "none", "output": "No LLM provider is available"}],
            "pending_confirmation": None,
        }

    graph = build_graph(services=services)
    # session_id 用作 checkpoint thread，维持同一会话状态。
    # 显式为本轮创建 executor，避免默认全局线程池在取消后阻塞解释器退出。
    config = {"configurable": {"thread_id": session_id}, "max_concurrency": 4}
    try:
        result = await asyncio.wait_for(graph.ainvoke(initial_state, config), timeout=settings.run_timeout_s)
    finally:
        aclose = getattr(graph, "aclose", None)
        if aclose is not None:
            await aclose()
    if result.get("messages") and result["messages"][-1].get("role") == "user":
        result["messages"][-1]["role"] = "assistant"
        result["messages"][-1]["content"] = str(result.get("tool_results", [{}])[-1].get("output", ""))

    successful_outputs = [
        output
        for item in result.get("tool_results", [])
        if (output := item.get("output")) is not None
        and (not isinstance(output, dict) or output.get("status") == "ok")
    ]
    if result.get("status") in {"success", "completed"} and successful_outputs and services is not None and hasattr(services.store, "upsert_session_summary"):
        evidence = "; ".join(
            f"operation={output.get('operation')} path={output.get('path')}" if isinstance(output, dict) and output.get("path") else str(output)[:120]
            for output in successful_outputs[-3:]
        )
        services.store.upsert_session_summary(
            session_id,
            f"{summary} {evidence}".strip()[:2000],
            owner_user_id=context.user_id,
            tenant_id=context.tenant_id,
        )
    return result
