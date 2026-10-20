import asyncio
import os

import pytest

from person_agents.graph.runtime import GraphServices

pytest.importorskip("langgraph")


async def test_supervisor_routes_chat_to_execution_agent():
    from person_agents.graph.engine import supervisor_node

    state = {"intent": "chat"}

    result = await supervisor_node(state)

    assert result["current_agent"] == "executor"
    assert result["agent_history"] == ["supervisor"]


async def test_supervisor_routes_memory_to_memory_agent():
    from person_agents.graph.engine import supervisor_node

    state = {"intent": "memory_write"}

    result = await supervisor_node(state)

    assert result["current_agent"] == "memory"
    assert result["agent_history"] == ["supervisor"]


async def test_execution_specialist_attributes_completion():
    from person_agents.graph.engine import execution_specialist

    state = {"intent": "chat", "agent_history": ["supervisor"]}

    result = await execution_specialist(state)

    assert result["current_agent"] == "executor"
    assert result["agent_history"] == ["supervisor", "executor"]


async def test_graph_execution_does_not_duplicate_executor_history():
    from person_agents.graph.engine import execution_specialist

    state = {"intent": "chat", "agent_history": ["supervisor"]}
    first = await execution_specialist(state)
    second = await execution_specialist({"intent": "chat", **first})

    assert second["agent_history"] == ["supervisor", "executor"]


@pytest.mark.skipif(
    os.environ.get("RUN_LANGGRAPH_E2E") != "true",
    reason="RUN_LANGGRAPH_E2E is not enabled",
)
async def test_compiled_graph_contains_multi_agent_topology():
    from langgraph.checkpoint.memory import MemorySaver

    from person_agents.graph.engine import build_graph

    graph = await asyncio.wait_for(
        asyncio.to_thread(build_graph, checkpointer=MemorySaver()),
        timeout=3,
    )

    assert {"supervisor", "execution_agent", "memory_write"} <= set(graph.get_graph().nodes)


async def test_build_graph_uses_injected_services_without_module_globals():
    from person_agents.graph.engine import build_graph
    from person_agents.graph.nodes import _registry, _store, _vector

    services = GraphServices(store=object(), vector=object(), registry=object())

    graph = build_graph(services=services)

    assert graph is not None
    assert _store is None
    assert _vector is None
    assert _registry is None


async def test_default_graph_contains_tool_specialist():
    from langgraph.checkpoint.memory import MemorySaver

    from person_agents.graph.engine import build_graph

    graph = build_graph(checkpointer=MemorySaver())

    assert "tool_specialist" in set(graph.get_graph().nodes)
    edges = {
        (edge.source, edge.target)
        for edge in graph.get_graph().edges
        if edge.source == "planner"
    }
    assert ("planner", "tool_specialist") in edges


async def test_tool_specialist_uses_injected_registry_and_preserves_attributes(monkeypatch):
    from person_agents.graph.engine import tool_specialist

    class Tool:
        name = "echo"
        description = "echo"
        def __init__(self):
            self.parameters = {}
        requires_confirm = False

        async def handler(self, arguments):
            return arguments

    class Registry:
        def get(self, name):
            return Tool() if name == "echo" else None

    async def successful_tool_call(tool, arguments, handler, **kwargs):
        return await handler(arguments)

    monkeypatch.setattr("person_agents.graph.engine.governed_tool_call", successful_tool_call)

    state = {
        "agent_history": ["supervisor"],
        "current_step": 0,
        "plan": [{"action": "hello", "tool": "echo", "arguments": {"text": "hello"}}],
    }

    result = await tool_specialist(state, registry=Registry())

    assert result["tool_results"][-1]["tool"] == "echo"
    assert result["current_agent"] == "tool"
    assert result["agent_history"][-1] == "tool"


async def test_tool_specialist_preserves_confirmation_payload(monkeypatch):
    from person_agents.graph.engine import tool_specialist

    class Tool:
        def __init__(self):
            self.parameters = {}
            self.requires_confirm = True

        async def handler(self, arguments):
            return arguments

    class Registry:
        def get(self, name):
            return Tool()

    async def confirmation_tool_call(tool, arguments, handler, **kwargs):
        return {"status": "confirmation_required", "confirmation_token": "token"}

    monkeypatch.setattr("person_agents.graph.engine.governed_tool_call", confirmation_tool_call)

    result = await tool_specialist(
        {
            "agent_history": ["supervisor"],
            "current_step": 0,
            "plan": [{"action": "write", "tool": "filesystem", "arguments": {}}],
        },
        registry=Registry(),
    )

    assert result["tool_results"][0]["output"] == {
        "status": "confirmation_required",
        "confirmation_token": "token",
    }


async def test_intent_node_uses_injected_vector(monkeypatch):
    from person_agents.graph.nodes import intent_node

    class Vector:
        def query(self, *args, **kwargs):
            return [{"text": "injected"}]

    class Response:
        content = "chat"
        degraded = False

    async def response_call(messages):
        return Response()

    monkeypatch.setattr("person_agents.graph.nodes.call_with_fallback", response_call)

    result = await intent_node(
        {"messages": [{"role": "user", "content": "hello"}], "owner_user_id": "user", "tenant_id": "tenant", "session_id": "session"},
        vector=Vector(),
    )

    assert result["memory_context"] == [{"text": "injected"}]


async def test_planner_node_uses_injected_registry(monkeypatch):
    from person_agents.graph.nodes import planner_node

    class Tool:
        name = "echo"
        description = "echo tool"

        def __init__(self):
            self.parameters = {"type": "object"}

    class Registry:
        def list_tools(self):
            return [Tool()]

    class Response:
        content = '[{"action":"hello","tool":"none"}]'
        degraded = False

    async def response_call(messages):
        assert "echo tool" in messages[0]["content"]
        return Response()

    monkeypatch.setattr("person_agents.graph.nodes.call_with_fallback", response_call)

    result = await planner_node({"messages": [{"role": "user", "content": "hello"}]}, registry=Registry())

    assert result["plan"][0]["tool"] == "none"


async def test_memory_node_uses_injected_services():
    from person_agents.graph.nodes import memory_write_node

    class Store:
        def upsert(self, *args, **kwargs):
            return "store"

    class Vector:
        def add(self, *args, **kwargs):
            return "vector"

    result = await memory_write_node(
        {
            "critic_feedback": "success",
            "messages": [{"role": "user", "content": "hello"}],
            "tool_results": [{"output": "done"}],
            "owner_user_id": "user",
            "tenant_id": "tenant",
            "session_id": "session",
        },
        store=Store(),
        vector=Vector(),
    )

    assert result == {}


@pytest.mark.skipif(
    os.environ.get("RUN_LANGGRAPH_E2E") != "true",
    reason="RUN_LANGGRAPH_E2E is not enabled",
)
async def test_injected_graph_uses_service_bound_nodes():
    from person_agents.graph.engine import build_graph

    class Vector:
        def query(self, *args, **kwargs):
            return [{"text": "graph-injected"}]

    class Store:
        pass

    class Registry:
        def list_tools(self):
            return []

    services = GraphServices(store=Store(), vector=Vector(), registry=Registry())
    graph = build_graph(services=services)

    assert graph is not None


@pytest.mark.skipif(
    os.environ.get("RUN_LANGGRAPH_E2E") != "true",
    reason="RUN_LANGGRAPH_E2E is not enabled",
)
async def test_injected_graph_end_to_end_tool_flow(tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver

    from person_agents.config.settings import settings
    from person_agents.graph.engine import build_graph
    from person_agents.observability import langfuse

    monkeypatch.setattr(settings, "langfuse_public_key", "", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "", raising=False)
    langfuse._client = None
    monkeypatch.setattr(langfuse, "trace_graph_node", lambda *args, **kwargs: None)

    class FakeResponse:
        def __init__(self, content):
            self.content = content
            self.degraded = False

    async def fake_llm(messages):
        if "Classify the user's intent" in messages[0]["content"]:
            return FakeResponse("tool_use")
        return FakeResponse('[{"action":"hello","tool":"echo","arguments":{"text":"hello"}}]')

    async def fake_critic_llm(messages):
        return FakeResponse("success")

    class FakeTool:
        name = "echo"
        description = "echo"

        def __init__(self):
            self.parameters = {"type": "object"}
            self.requires_confirm = False

        async def handler(self, arguments):
            return {"echo": arguments["text"]}

    class Registry:
        def list_tools(self):
            return [FakeTool()]

        def get(self, name):
            return FakeTool() if name == "echo" else None

    class Vector:
        def query(self, *args, **kwargs):
            return []

        def add(self, *args, **kwargs):
            return True

    class Store:
        def upsert(self, *args, **kwargs):
            return True

    async def governed_tool(tool, arguments, handler, **kwargs):
        return await handler(arguments)

    async def governed_llm(messages, call_fn, context):
        return await fake_llm(messages)

    monkeypatch.setattr("person_agents.graph.engine.governed_tool_call", governed_tool)
    monkeypatch.setattr("person_agents.graph.nodes.call_with_fallback", fake_llm)
    monkeypatch.setattr("person_agents.graph.nodes.governed_llm_call", governed_llm)
    monkeypatch.setattr("person_agents.middleware.trace_llm_call", lambda *args, **kwargs: None)

    services = GraphServices(store=Store(), vector=Vector(), registry=Registry())
    graph = build_graph(checkpointer=MemorySaver(), services=services)

    result = await asyncio.wait_for(graph.ainvoke(
        {
            "messages": [{"role": "user", "content": "hello"}],
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
            "session_id": "e2e",
            "tenant_id": "tenant",
            "owner_user_id": "user",
            "current_agent": "",
            "agent_history": [],
        },
        {"configurable": {"thread_id": "e2e"}},
    ), timeout=3)

    assert result["current_agent"] == "tool"
    assert result["agent_history"] == ["supervisor", "executor", "tool"]
    assert result["status"] == "success"


@pytest.mark.skipif(
    os.environ.get("RUN_LANGGRAPH_E2E") != "true",
    reason="RUN_LANGGRAPH_E2E is not enabled",
)
async def test_injected_tool_specialist_is_wired_into_graph():
    from person_agents.graph.engine import build_graph

    services = GraphServices(store=object(), vector=object(), registry=object())

    graph = build_graph(services=services)

    assert "tool_specialist" in set(graph.get_graph().nodes)


async def test_run_graph_accepts_services_and_preserves_api_contract(monkeypatch):
    from person_agents.graph.engine import run_graph
    from person_agents.security.context import RequestContext

    def fake_build_graph(checkpointer=None, services=None):
        assert services is not None

        async def graph_ainvoke(state, config):
            assert state["owner_user_id"] == "user"
            return {"current_agent": "executor", "agent_history": ["supervisor", "executor"]}

        return type("FakeGraph", (), {"ainvoke": staticmethod(graph_ainvoke)})()

    monkeypatch.setattr("person_agents.graph.engine.build_graph", fake_build_graph)

    result = await run_graph(
        "hello",
        session_id="session",
        context=RequestContext(user_id="user", tenant_id="tenant", session_id="session"),
        services=GraphServices(store=object(), vector=object(), registry=object()),
    )

    assert result["current_agent"] == "executor"


async def test_planner_routes_plain_actions_to_execution_agent():
    from person_agents.graph.edges import route_after_planner

    assert route_after_planner({"plan": [{"tool": "none"}], "current_step": 0}) == "execution_agent"
    assert route_after_planner({"plan": [{"tool": "filesystem"}], "current_step": 0}) == "tool_specialist"


async def test_fast_reply_short_circuits_greeting(monkeypatch):
    from person_agents.graph import engine
    from person_agents.graph.engine import run_graph

    def fail_build(*args, **kwargs):
        raise AssertionError("greeting must not build the graph")

    monkeypatch.setattr(engine, "build_graph", fail_build)

    result = await run_graph(
        "Hi there",
        session_id="fast",
        services=GraphServices(store=object(), vector=object(), registry=object()),
    )

    assert result["intent"] == "chat"
    assert result["status"] == "completed"
    assert result["tool_results"][-1]["output"] == "Hello! How can I help you today?"


async def test_loop_guard_stops_after_elapsed_threshold(monkeypatch):
    from person_agents.graph.engine import route_after_critic
    from person_agents.graph.nodes import critic_node

    async def governed_llm_call(*args, **kwargs):
        class Response:
            content = "retry"

        return Response()

    monkeypatch.setattr("person_agents.graph.nodes.governed_llm_call", governed_llm_call)
    state = {
        "messages": [],
        "tool_results": [{"output": "partial"}],
        "started_at": 0.0,
        "retry_count": 1,
        "session_id": "loop",
        "tenant_id": "tenant",
        "owner_user_id": "user",
    }

    result = await critic_node(state)

    assert result["status"] == "failed"
    assert result["degraded"] is True
    assert result["error"] == "Long-running agent loop stopped"
    assert route_after_critic({**state, **result}) == "memory_write"


async def test_worker_uses_injected_services_for_graph(monkeypatch):
    from person_agents.memory.postgres_store import PostgresStore
    from person_agents.runtime.worker import run_one

    captured_services = {}

    async def fake_run_graph(prompt, session_id, context, services=None):
        captured_services["services"] = services
        return {"current_agent": "tool", "agent_history": ["supervisor", "tool"]}

    class Store(PostgresStore):
        def __init__(self):
            pass

        async def renew_run_lease(self, *args, **kwargs):
            return True

        async def get_run(self, *args, **kwargs):
            return {"status": "running"}

        async def update_run(self, *args, **kwargs):
            return True

    monkeypatch.setattr("person_agents.graph.engine.run_graph", fake_run_graph)

    await run_one(
        {"run_id": "run", "owner_user_id": "user", "tenant_id": "tenant", "session_id": "session", "prompt": "hello", "attempts": 1},
        Store(),
        services=GraphServices(store=object(), vector=object(), registry=object()),
    )

    assert captured_services["services"] is not None


async def test_cli_run_uses_injected_services(monkeypatch):
    from person_agents.cli import _run_agent_prompt

    expected = GraphServices(store=object(), vector=object(), registry=object())
    captured = {}

    async def fake_run_graph(prompt, session_id, context, services=None):
        captured["services"] = services
        return {"tool_results": [{"output": "ok"}]}

    monkeypatch.setattr("person_agents.graph.engine.run_graph", fake_run_graph)
    monkeypatch.setattr(
        "person_agents.graph.runtime.GraphServices.default",
        staticmethod(lambda: expected),
    )

    result = await _run_agent_prompt("hello", "cli-run")

    assert result == "ok"
    assert captured["services"] is expected


async def test_cli_chat_uses_injected_services(monkeypatch):
    from person_agents.cli import _run_agent_prompt

    expected = GraphServices(store=object(), vector=object(), registry=object())
    calls = []

    async def fake_run_graph(prompt, session_id, context, services=None):
        calls.append((session_id, services))
        return {"tool_results": [{"output": prompt}]}

    monkeypatch.setattr("person_agents.graph.engine.run_graph", fake_run_graph)
    monkeypatch.setattr(
        "person_agents.graph.runtime.GraphServices.default",
        staticmethod(lambda: expected),
    )

    assert await _run_agent_prompt("hello", "cli-chat") == "hello"
    assert calls == [("cli-chat", expected)]


async def test_api_run_uses_injected_services(monkeypatch):
    import hashlib

    import httpx

    from person_agents.api.app import app

    monkeypatch.setattr(
        "person_agents.config.settings.settings.api_key_hash",
        hashlib.sha256(b"test-key").hexdigest(),
        raising=False,
    )
    captured_services = {}

    async def fake_run_graph(prompt, session_id, context, services=None):
        captured_services["services"] = services
        return {"current_agent": "executor", "tool_results": [], "status": "completed"}

    monkeypatch.setattr("person_agents.api.app._run_graph", fake_run_graph)

    services = GraphServices(store=object(), vector=object(), registry=object())
    app.state.graph_services = services
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post(
            "/runs",
            json={"prompt": "hello"},
            headers={"X-API-Key": "test-key"},
        )

    assert response.status_code == 200
    assert captured_services["services"] is services


async def test_api_lifespan_installs_default_graph_services(monkeypatch):
    from person_agents.api.app import app
    from person_agents.graph.runtime import GraphServices

    expected = GraphServices(store=object(), vector=object(), registry=object())
    monkeypatch.setattr(
        "person_agents.graph.runtime.GraphServices.default",
        staticmethod(lambda: expected),
    )

    async with app.router.lifespan_context(app):
        assert app.state.graph_services is expected

    assert getattr(app.state, "graph_services", None) is expected

@pytest.mark.skipif(
    os.environ.get("RUN_LANGGRAPH_E2E") != "true",
    reason="RUN_LANGGRAPH_E2E is not enabled",
)
async def test_injected_graph_routes_tool_step_to_specialist():
    from person_agents.graph.engine import build_graph
    from person_agents.graph.nodes import _registry

    services = GraphServices(store=object(), vector=object(), registry=object())
    graph = build_graph(services=services)

    assert _registry is None
    assert "tool_specialist" in set(graph.get_graph().nodes)
    edges = {
        (edge.source, edge.target)
        for edge in graph.get_graph().edges
        if edge.source == "planner"
    }

    assert ("planner", "tool_specialist") in edges
