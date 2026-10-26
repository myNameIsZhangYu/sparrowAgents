import asyncio
import json

from langgraph.checkpoint.memory import MemorySaver

from person_agents.graph.engine import build_graph
from person_agents.graph.render import render_agent_result
from person_agents.graph.runtime import GraphServices
from person_agents.tools.registry import ToolRegistry, register_default_tools


class FakeResponse:
    def __init__(self, content: str):
        self.content = content
        self.degraded = False
        self.model = "fake"
        self.prompt_tokens = 0
        self.completion_tokens = 0


class MemoryVector:
    def query(self, *args, **kwargs):
        return []

    def add(self, *args, **kwargs):
        return True


class MemoryStore:
    def upsert(self, *args, **kwargs):
        return True


def _state(prompt: str, session_id: str = "resume-e2e"):
    return {
        "messages": [{"role": "user", "content": prompt}],
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
        "session_id": session_id,
        "tenant_id": "tenant",
        "owner_user_id": "user",
        "current_agent": "",
        "agent_history": [],
        "pending_confirmation": None,
        "subtasks": [],
        "pending_subtask_ids": [],
        "subtask_results": [],
    }


async def _invoke_graph(prompt, services, monkeypatch, plans, session_id="resume-e2e"):
    async def fake_llm(messages):
        content = plans.pop(0) if plans else "success"
        return FakeResponse(content)

    async def governed_llm(messages, call_fn, context):
        if "Evaluate if the task was completed successfully" in messages[0]["content"]:
            return FakeResponse("success")
        return await fake_llm(messages)

    monkeypatch.setattr("person_agents.graph.nodes.call_with_fallback", fake_llm)
    monkeypatch.setattr("person_agents.graph.nodes.governed_llm_call", governed_llm)
    graph = build_graph(checkpointer=MemorySaver(), services=services)
    try:
        return await asyncio.wait_for(
            graph.ainvoke(_state(prompt, session_id), {"configurable": {"thread_id": session_id}}),
            5,
        )
    finally:
        pass


async def test_two_step_confirmation_resumes_remaining_plan(tmp_path, monkeypatch):
    registry = ToolRegistry()
    register_default_tools(registry, workspace_root=str(tmp_path))
    services = GraphServices(store=MemoryStore(), vector=MemoryVector(), registry=registry)
    content = "hello\n"
    plans = [
        json.dumps([
            {"action": "write", "tool": "filesystem", "arguments": {"operation": "write", "path": "first.txt", "content": content}},
            {"action": "read", "tool": "filesystem", "arguments": {"operation": "read", "path": "first.txt"}},
        ]),
    ]
    result = await _invoke_graph("创建 first.txt 然后读取", services, monkeypatch, plans)

    assert result["status"] == "confirmation_required"
    assert not (tmp_path / "first.txt").exists()
    token = result["pending_confirmation"]
    assert token in render_agent_result(result)

    from person_agents.graph.tool_confirmation import confirm_pending_tool
    from person_agents.security.context import RequestContext

    confirm = await confirm_pending_tool(
        token,
        context=RequestContext(user_id="user", tenant_id="tenant", session_id="resume-e2e"),
        registry=registry,
    )
    assert confirm.get("status") == "ok"
    assert (tmp_path / "first.txt").exists()

    plans = [json.dumps([{"action": "直接回复", "tool": "none"}])]
    resumed = await _invoke_graph("继续", services, monkeypatch, plans)
    assert resumed["status"] in {"success", "completed", "confirmation_required"}
    assert resumed["current_step"] >= 1


def test_normalize_planner_plan_requires_clarification_for_invalid_steps():
    from person_agents.graph.nodes import normalize_planner_plan

    unknown = normalize_planner_plan([{"action": "do", "tool": "unknown"}], "do")
    assert unknown[0]["arguments"].get("needs_clarification") is True
    missing_path = normalize_planner_plan(
        [{"action": "write", "tool": "filesystem", "arguments": {"operation": "write", "content": "hi"}}],
        "write",
    )
    assert missing_path[0]["arguments"] == {"operation": "read", "path": "write"}
