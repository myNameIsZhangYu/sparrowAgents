import json
import time
from typing import Any

import structlog

from person_agents.config.settings import settings
from person_agents.governance.policy import PolicyEngine
from person_agents.graph.state import AgentState
from person_agents.graph.task_graph import (
    build_failure_analysis,
    classify_failure_type,
    execute_task_graph,
    merge_subtask_results,
    merge_subtask_results_with_arbitration,
    normalize_plan_to_task_graph,
    retry_failed_subtasks,
)
from person_agents.llm.fallback import call_with_fallback
from person_agents.memory.sqlite_store import SQLiteStore
from person_agents.memory.vector_store import VectorStore
from person_agents.middleware import governed_llm_call, governed_tool_call
from person_agents.observability.langfuse import trace_graph_node
from person_agents.observability.metrics import (
    agent_loop_breaks_total,
    agent_run_duration_seconds,
    agent_runs_total,
)
from person_agents.security.context import RequestContext
from person_agents.tools.registry import ToolRegistry, register_default_tools

logger = structlog.get_logger()

TOOL_TASK_PHRASES = (
    "创建python文件",
    "创建 python 文件",
    "新建",
    "写入",
    "写文件",
    "保存文件",
    "读取",
    "读文件",
    "列出",
    "列出文件",
    "删除文件",
    "write ",
    "create ",
    "read ",
    "list files",
    "delete file",
)


def looks_like_tool_task(text: str) -> bool:
    normalized = text.strip().lower()
    return any(phrase in normalized for phrase in TOOL_TASK_PHRASES)


def build_planner_context(messages, tool_results, summary=""):
    items = []
    for message in messages[-5:]:
        items.append(f"{message.get('role')}: {str(message.get('content', ''))[:150]}")
    for result in tool_results[-5:]:
        output = result.get("output", "")
        if isinstance(output, dict):
            path = output.get("path")
            operation = output.get("operation")
            text = f"operation={operation} path={path}" if path else json.dumps(output, ensure_ascii=False)
        else:
            text = str(output)
        items.append(f"tool: {text[:150]}")
    if summary:
        items.append(f"summary: {str(summary)[:150]}")
    return "\n".join(items)[-600:]


def normalize_planner_plan(raw_plan: Any, user_input: str) -> list[dict[str, Any]]:
    if not isinstance(raw_plan, list) or not raw_plan:
        return [{"action": user_input, "tool": "none", "depends_on": []}]

    normalized: list[dict[str, Any]] = []
    previous_id: str | None = None
    for index, raw_step in enumerate(raw_plan):
        step = raw_step if isinstance(raw_step, dict) else {"action": str(raw_step)}
        action = str(step.get("action") or user_input)
        tool = str(step.get("tool") or "none")
        arguments = step.get("arguments")
        if tool == "filesystem" and not isinstance(arguments, dict):
            arguments = {"operation": "read", "path": action.strip()}
        elif not isinstance(arguments, dict):
            arguments = {}
        if tool == "filesystem" and arguments.get("operation") == "write" and not arguments.get("path"):
            arguments = {"operation": "read", "path": action.strip()}
        elif tool not in {"none", "filesystem", "shell"}:
            arguments = {**arguments, "needs_clarification": True}
        normalized.append({
            "action": action,
            "tool": tool,
            "arguments": arguments,
            "depends_on": step.get("depends_on", [previous_id] if previous_id and index > 0 else []),
        })
        previous_id = f"task-{index}"
    return normalized


async def run_tool_step(
    step: dict[str, Any],
    state: AgentState,
    registry: ToolRegistry | None = None,
    *,
    services: Any | None = None,
) -> dict[str, Any]:
    tool = step.get("tool", "none")
    resolved_registry = registry or (services.registry if services else None)
    tool_def = resolved_registry.get(tool) if resolved_registry else None
    context = RequestContext(
        user_id=state.get("owner_user_id", state.get("session_id", "local")),
        tenant_id=state.get("tenant_id", "default"),
        session_id=state.get("session_id", "cli"),
    )
    if not tool_def:
        return {"output": f"Unknown tool: {tool}", "degraded": True, "error": f"Unknown tool: {tool}"}
    result = await governed_tool_call(
        tool,
        step.get("arguments") or {},
        tool_def.handler,
        context=context,
        tool_schema=tool_def.parameters,
        requires_confirmation=tool_def.requires_confirm,
    )
    output = result if isinstance(result, dict) and result.get("status") == "confirmation_required" else str(result)
    if not isinstance(output, dict):
        output_allowed, governed_output = PolicyEngine().sanitize_tool_output(output)
        if not output_allowed:
            return {"output": governed_output, "degraded": True, "error": governed_output}
    return {"output": output, "degraded": False, "error": None}

# 这些模块级单例只作为 CLI / 测试回退；API 和 worker 应优先使用注入服务。
_store: SQLiteStore | None = None
_vector: VectorStore | None = None
_registry: ToolRegistry | None = None


def _get_store() -> SQLiteStore:
    global _store
    if _store is None:
        # 延迟创建，避免导入阶段打开数据库。
        _store = SQLiteStore()
    return _store


def _get_vector() -> VectorStore:
    global _vector
    if _vector is None:
        # Chroma 初始化成本较高，同样延迟到首次使用。
        _vector = VectorStore()
    return _vector


def _get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        # 默认注册 filesystem 和 shell，实际执行仍受权限与确认限制。
        register_default_tools(_registry)
    return _registry


def _recent_messages(state: AgentState, limit: int = 10) -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in state.get("messages", [])[-limit:]
        if message.get("role") in ("user", "assistant", "system") and "content" in message
    ]


async def intent_node(state: AgentState, vector: VectorStore | None = None) -> dict[str, Any]:
    started = time.monotonic()
    # 取最新用户消息作为意图分类输入。
    user_input = state["messages"][-1]["content"]
    # 内容治理失败时直接降级，不再进入 LLM。
    allowed, governance_reason = PolicyEngine().check_content(user_input)
    if not allowed:
        return {"intent": "chat", "degraded": True, "memory_context": [], "status": "failed", "error": governance_reason}
    if looks_like_tool_task(user_input):
        output = {"intent": "task", "degraded": False, "memory_context": []}
        trace_graph_node(state.get("session_id", "agent"), "intent", {"input": user_input}, output, int((time.monotonic() - started) * 1000))
        return output
    context = RequestContext(
        user_id=state.get("owner_user_id", state.get("session_id", "local")),
        tenant_id=state.get("tenant_id", "default"),
        session_id=state.get("session_id", "cli"),
    )
    resolved_vector = vector or _get_vector()
    if settings.testing and vector is None:
        # 测试/评估禁用外部集成，避免真实向量库模型下载阻塞事件循环。
        memory_hits = []
    else:
        try:
            # 向量查询是同步库，放到线程里并限时，防止阻塞事件循环。
            memory_hits = resolved_vector.query(
                user_input,
                n_results=3,
                owner_user_id=context.user_id,
                tenant_id=context.tenant_id,
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            # 记忆检索失败不阻断主流程，仅退化为无上下文运行。
            logger.warning("vector_query_failed", timeout_s=settings.vector_timeout_s, error=str(exc))
            memory_hits = []
    memory_text = "\n".join(f"- {m['text']}" for m in memory_hits if m.get("text"))
    if looks_like_tool_task(user_input):
        intent = "task"
        output = {"intent": intent, "degraded": False, "memory_context": memory_hits}
        trace_graph_node(state.get("session_id", "agent"), "intent", {"input": user_input}, output, int((time.monotonic() - started) * 1000))
        return output
    resp = await call_with_fallback([
        {"role": "system", "content": f"Classify the user's intent as one of: chat, task, memory_write, tool_use. Return ONLY the intent word.\n\nRelevant memory context:\n{memory_text}"},
        {"role": "user", "content": user_input},
    ])
    intent = resp.content.strip().lower()
    # LLM 输出不可信，非法意图必须收敛到默认 chat。
    if intent not in ("chat", "task", "memory_write", "tool_use"):
        intent = "chat"
    output = {"intent": intent, "degraded": resp.degraded, "memory_context": memory_hits}
    trace_graph_node(state.get("session_id", "agent"), "intent", {"input": user_input}, output, int((time.monotonic() - started) * 1000))
    return output


async def planner_node(state: AgentState, registry: ToolRegistry | None = None, services: Any | None = None) -> dict[str, Any]:
    started = time.monotonic()
    user_input = state["messages"][-1]["content"]
    memory_context = "\n".join(f"- {m['text']}" for m in state.get("memory_context", []) if m.get("text"))
    conversation = _recent_messages(state)
    previous_plan = "\n".join(
        f"- {json.dumps(step, ensure_ascii=False)}"
        for step in state.get("plan", [])
    )
    def _result_text(result: dict[str, Any]) -> str:
        output = result.get("output", "")
        if isinstance(output, dict):
            path = output.get("path")
            operation = output.get("operation")
            evidence = f"operation={operation} path={path}" if path else json.dumps(output, ensure_ascii=False)
            return str(evidence)[:300]
        return str(output)[:300]

    previous_results = "\n".join(
        f"- step {result.get('step')}: {_result_text(result)}"
        for result in state.get("tool_results", [])
    )
    prior_conversation = [
        {"role": "assistant", "content": _result_text(result)}
        for result in state.get("tool_results", [])
    ]
    session_summary = state.get("session_summary", "")
    planner_context = build_planner_context(
        state.get("messages", []),
        state.get("tool_results", []),
        session_summary,
    )
    resolved_registry = registry or (services.registry if services else None)
    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
        for tool in (resolved_registry if resolved_registry is not None else _get_registry()).list_tools()
    ]
    # planner 必须返回 JSON 数组；工具 schema 放进提示词帮助模型生成参数。
    resp = await call_with_fallback([
        {
            "role": "system",
            "content": (
                "You are the planning agent. Decompose the objective into ordered, independently executable steps, "
                "and delegate only steps that need a tool to the matching specialist. Return ONLY a JSON array of objects "
                "with 'action', 'tool', optional 'arguments', and optional 'depends_on' fields. "
                "Every non-first step must declare 'depends_on' unless it can safely run in parallel. "
                "Use 'none' only for direct LLM actions. "
                f"Session context: {planner_context}\n"
                f"Available tools: {tools}"
            ),
        },
        *conversation[:-1],
        *prior_conversation[-3:],
        {
            "role": "user",
            "content": (
                f"Task: {user_input}\nRelevant memory:\n{memory_context}\n\n"
                f"Previous plan:\n{previous_plan}\n\nPrevious execution results:\n{previous_results}"
            ),
        },
    ])
    # 只接受合法 JSON；优先剥离模型常会附加的 markdown fence。
    planner_output = resp.content.strip()
    if planner_output.startswith("```"):
        planner_output = planner_output.removeprefix("```json").removeprefix("```")
        planner_output = planner_output.split("```", 1)[0]
    try:
        parsed_plan = json.loads(planner_output)
    except json.JSONDecodeError:
        parsed_plan = None
    plan = normalize_planner_plan(parsed_plan, user_input)
    output = {"plan": plan, "current_step": 0}
    trace_graph_node(state.get("session_id", "agent"), "planner", {"input": user_input}, output, int((time.monotonic() - started) * 1000))
    return output


async def executor_node(state: AgentState, services: Any | None = None) -> dict[str, Any]:
    started = time.monotonic()
    # executor 的 LLM 调用和工具调用都使用租户上下文限流。
    graph_context = RequestContext(
        user_id=state.get("owner_user_id", state.get("session_id", "local")),
        tenant_id=state.get("tenant_id", "default"),
        session_id=state.get("session_id", "cli"),
    )
    plan = state.get("plan", [])
    if len(plan) > 1:
        subtasks = state.get("subtasks") or normalize_plan_to_task_graph(plan, state["messages"][-1]["content"])
        if any(task.get("status") == "failed" for task in subtasks):
            retried = retry_failed_subtasks(subtasks)
            retried_by_id = {task["id"]: task for task in retried}
            subtasks = [
                retried_by_id[task["id"]]
                if task.get("status") == "failed" and task["id"] in retried_by_id
                else task
                for task in subtasks
            ]

        async def run_subtask(subtask: dict[str, Any]) -> dict[str, Any]:
            if subtask["tool"] != "none":
                tool_result = await run_tool_step(subtask, state, services=services)
                output = tool_result["output"]
                result_status = "failed" if tool_result.get("degraded") else "completed"
                return {
                    "output": output,
                    "status": result_status,
                    "degraded": tool_result.get("degraded", False),
                    "error": tool_result.get("error"),
                }
            else:
                async def _llm(messages):
                    return await call_with_fallback(messages)

                resp = await governed_llm_call([
                    {
                        "role": "system",
                        "content": "You are the execution agent. Complete only the delegated action. Do not replan.",
                    },
                    {"role": "user", "content": subtask["action"]},
                ], _llm, graph_context)
                output = resp.content
            return {"output": output}

        execution_tasks = [task for task in subtasks if task.get("status") != "completed"]
        task_results = await execute_task_graph(execution_tasks, run_subtask)
        task_lookup = {task["id"]: task for task in subtasks}
        pending_confirmation = next(
            (
                result["output"].get("confirmation_token")
                for result in task_results
                if isinstance(result.get("output"), dict) and result["output"].get("status") == "confirmation_required"
            ),
            None,
        )
        arbitrated_results = merge_subtask_results_with_arbitration([*task_results, *state.get("subtask_results", [])])
        tool_results = [
            {
                "step": index,
                "subtask_id": result["subtask_id"],
                "action": task_lookup[result["subtask_id"]]["action"],
                "tool": task_lookup[result["subtask_id"]]["tool"],
                "output": result["output"],
                "status": result["status"],
            }
            for index, result in enumerate(arbitrated_results)
        ]
        result_status = {result["subtask_id"]: result["status"] for result in task_results}
        for subtask in subtasks:
            subtask["status"] = result_status.get(subtask["id"], subtask.get("status", "pending"))
        trace_graph_node(
            state.get("session_id", "agent"),
            "executor",
            {"subtasks": len(subtasks)},
            {"results": tool_results},
            int((time.monotonic() - started) * 1000),
        )
        if pending_confirmation:
            return {
                "subtasks": subtasks,
                "subtask_results": task_results,
                "tool_results": tool_results,
                "current_step": len(plan),
                "status": "confirmation_required",
                "pending_confirmation": pending_confirmation,
            }
        return {
            "subtasks": subtasks,
            "subtask_results": task_results,
            "tool_results": tool_results,
            "current_step": len(plan),
        }

    if not plan or state["current_step"] >= len(plan):
        # planner 未提供计划时退化为一次普通对话。
        user_input = state["messages"][-1]["content"]
        async def _llm(messages):
            return await call_with_fallback(messages)
        resp = await governed_llm_call([
            {
                "role": "system",
                "content": "You are a helpful assistant. Use the conversation history to resolve pronouns and follow-up requests.",
            },
            *state.get("messages", [])[-10:],
        ], _llm, graph_context)
        result = {"step": 0, "action": user_input, "tool": "none", "output": resp.content}
        trace_graph_node(state.get("session_id", "agent"), "executor", {"input": user_input}, result, int((time.monotonic() - started) * 1000))
        return {"tool_results": [result], "current_step": 1}

    step = state["plan"][state["current_step"]]
    action = step.get("action", "")
    tool = step.get("tool", "none")
    conversation = state.get("messages", [])
    context = "\n".join(f"- {m['text']}" for m in state.get("memory_context", []) if m.get("text"))
    prior_results = merge_subtask_results([
        {**result, "subtask_id": f"task-{result.get('step', index)}"}
        for index, result in enumerate(state.get("tool_results", []))
    ])
    execution_context = "\n".join(
        f"step {result.get('step')}: {result.get('output', '')[:500]}"
        for result in prior_results
    )

    if tool != "none":
        tool_def = services.registry.get(tool) if services else None
        if tool_def is None:
            tool_def = _get_registry().get(tool)
        if tool_def:
            # planner 未提供参数时不猜测语义，让工具 schema 校验返回明确错误。
            arguments = step.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {"operation": "read", "path": action.strip()}
            try:
                # 旧执行路径同样必须经过统一工具治理。
                tool_result = await governed_tool_call(
                    tool,
                    arguments,
                    tool_def.handler,
                    context=context,
                    tool_schema=tool_def.parameters,
                    requires_confirmation=tool_def.requires_confirm,
                )
                output = tool_result if isinstance(tool_result, dict) and tool_result.get("status") == "confirmation_required" else str(tool_result)
            except (PermissionError, RuntimeError) as exc:
                output = f"Tool execution rejected: {exc}"
                # 最后一步失败时直接标记 fail，避免无意义的 critic retry。
                if state["current_step"] + 1 >= len(plan):
                    failure_result = {"step": state["current_step"], "action": action, "tool": tool, "output": output}
                    return {
                        "tool_results": [failure_result],
                        "current_step": len(plan),
                        "critic_feedback": "fail",
                        "status": "fail",
                        "error": str(exc),
                    }
        else:
            # planner 引用了未注册工具，本次步骤输出错误信息。
            output = f"Unknown tool: {tool}"
    else:
        async def _llm(messages):
            return await call_with_fallback(messages)
        resp = await governed_llm_call([
            {
                "role": "system",
                "content": (
                    "You are the execution agent. Complete only the delegated action. Do not replan. "
                    f"Relevant memory:\n{context}\n\nCompleted results:\n{execution_context}"
                ),
            },
            *conversation[-10:],
            {"role": "user", "content": action},
        ], _llm, graph_context)
        output = resp.content

    result = {"step": state["current_step"], "action": action, "tool": tool, "output": output}
    trace_graph_node(state.get("session_id", "agent"), "executor", {"input": action, "tool": tool}, result, int((time.monotonic() - started) * 1000))
    return {"tool_results": [result], "current_step": state["current_step"] + 1}


async def critic_node(state: AgentState) -> dict[str, Any]:
    started = time.monotonic()
    started_at = state.get("started_at")
    if (
        started_at is not None
        and time.monotonic() - float(started_at) >= settings.loop_guard_seconds
    ):
        # retry 循环超过阈值时主动止损，避免消耗 token 和租户配额。
        agent_loop_breaks_total.labels("time_limit").inc()
        return {
            "critic_feedback": "fail",
            "status": "failed",
            "degraded": True,
            "error": "Long-running agent loop stopped",
        }
    graph_context = RequestContext(
        user_id=state.get("owner_user_id", state.get("session_id", "local")),
        tenant_id=state.get("tenant_id", "default"),
        session_id=state.get("session_id", "cli"),
    )
    tool_results = state.get("tool_results", [])
    results_summary = "\n".join(
        result.get("output", "")[:200] if isinstance(result.get("output", ""), str) else str(result.get("output", ""))[:200]
        for result in tool_results
    )
    conversation = _recent_messages(state)
    current_plan = "\n".join(
        f"- step {result.get('step')}: {result.get('action', '')} via {result.get('tool', '')}"
        for result in state.get("tool_results", [])
    )
    structured_failures = build_failure_analysis(state.get("subtasks", []), tool_results)
    if not structured_failures:
        structured_failures = [
            {
                "subtask_id": result.get("subtask_id", f"step-{result.get('step', index)}"),
                "failure_type": classify_failure_type({"status": "failed", "output": result.get("output", "")}),
                "reason": result.get("output", "unknown failure"),
                "attempt": 0,
            }
            for index, result in enumerate(tool_results)
            if "invalid" in str(result.get("output", "")).lower()
            or "rejected" in str(result.get("output", "")).lower()
            or "error" in str(result.get("output", "")).lower()
        ]
    failure_analysis = "\n".join(
        f"- {failure['subtask_id']} [{failure['failure_type']}]: {failure['reason']}"
        for failure in structured_failures
    )
    retry_count = state.get("retry_count", 0)
    failed_subtask_ids = [failure["subtask_id"] for failure in structured_failures]
    required_subtasks = state.get("subtasks", [])
    completed_subtasks = [
        result for result in state.get("subtask_results", []) if result.get("status") == "completed" and not result.get("degraded")
    ]
    if required_subtasks and not structured_failures and len(completed_subtasks) == len(required_subtasks):
        output = {
            "critic_feedback": "success",
            "status": "success",
            "retry_count": retry_count,
            "failed_subtask_ids": [],
            "failure_analysis": [],
            "recovery_strategy": "none",
        }
        trace_graph_node(state.get("session_id", "agent"), "critic", {"results": "structured_success"}, output, int((time.monotonic() - started) * 1000))
        agent_runs_total.labels(agent="critic", status="success").inc()
        agent_run_duration_seconds.labels(agent="critic").observe(time.monotonic() - started)
        return output
    async def _llm(messages):
        return await call_with_fallback(messages)

    resp = await governed_llm_call(
        [
            {"role": "system", "content": "Evaluate if the task was completed successfully. Return 'success' or 'retry' or 'fail'."},
            *conversation,
            {
                "role": "user",
                "content": (
                    f"Tool and execution results:\n{results_summary}\n\nCurrent plan:\n{current_plan}\n\n"
                    f"Retry count: {retry_count}\n\nFailure analysis:\n{failure_analysis}\n\n"
                    "If the failure is recoverable, return 'retry' and the graph will re-plan."
                ),
            },
        ],
        _llm,
        graph_context,
    )
    feedback = resp.content.strip().lower()
    if feedback not in ("success", "retry", "fail"):
        feedback = "fail"
    if feedback == "fail" and retry_count < 3 and failure_analysis:
        # 可恢复失败先重规划一次，而不是立即终止整个任务。
        feedback = "retry"
    # critic 的反馈直接驱动后续 retry 路由。
    output = {
        "critic_feedback": feedback,
        "status": feedback,
        "retry_count": retry_count + 1,
        "failed_subtask_ids": failed_subtask_ids,
        "failure_analysis": structured_failures,
        "recovery_strategy": "retry_failed_subtasks" if failed_subtask_ids else "none",
    }
    trace_graph_node(state.get("session_id", "agent"), "critic", {"results": str(results_summary)}, output, int((time.monotonic() - started) * 1000))
    agent_runs_total.labels(agent="critic", status="success").inc()
    agent_run_duration_seconds.labels(agent="critic").observe(time.monotonic() - started)
    return output


async def close_graph_runtime_resources() -> None:
    global _store, _vector, _registry
    if _store is not None and hasattr(_store, "close"):
        await _store.close()
    if _vector is not None and hasattr(_vector, "close"):
        _vector.close()
    _store = None
    _vector = None
    _registry = None


async def memory_write_node(state: AgentState, store: SQLiteStore | None = None, vector: VectorStore | None = None) -> dict[str, Any]:
    started = time.monotonic()
    # 只有 critic 判定成功时才写入长期记忆，避免污染向量库。
    if state.get("critic_feedback") == "success":
        user_input = state["messages"][-1]["content"]
        raw_result = state["tool_results"][-1].get("output", "")
        result = raw_result[:300] if isinstance(raw_result, str) else str(raw_result)[:300]
        key = f"memory_{hash(user_input)}"
        resolved_store = store or _get_store()
        resolved_vector = vector or _get_vector()
        resolved_store.upsert(
            key,
            result,
            category="experience",
            owner_user_id=state.get("owner_user_id", "local"),
            tenant_id=state.get("tenant_id", "default"),
        )
        resolved_vector.add(
            result,
            {"source": "agent", "session": state.get("session_id", "")},
            owner_user_id=state.get("owner_user_id", "local"),
            tenant_id=state.get("tenant_id", "default"),
        )
        logger.info("memory_written", key=key)
    agent_runs_total.labels(agent="memory", status="success").inc()
    agent_run_duration_seconds.labels(agent="memory").observe(time.monotonic() - started)
    return {}
