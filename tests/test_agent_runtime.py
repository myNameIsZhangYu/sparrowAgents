import pytest

from person_agents.graph.agent_runtime import (
    AgentMessageBus,
    AgentRuntime,
    AgentRuntimeRegistry,
    isolate_agent_context,
)


def test_agent_runtime_registry_serialization_roundtrip():
    registry = AgentRuntimeRegistry()
    registry.register(AgentRuntime(
        name="researcher",
        task_types=("tool_use",),
        allowed_tools=("mcp.search",),
        max_concurrency=3,
        persona="careful researcher",
        skills=("web-research",),
        memory_scope="tenant:user",
        state={"style": "concise"},
    ))

    config = registry.to_config()
    restored = AgentRuntimeRegistry.from_config(config)

    assert restored.get("researcher") == registry.get("researcher")
    assert config[0]["state"] == {"style": "concise"}


def test_agent_runtime_rejects_invalid_contract():
    registry = AgentRuntimeRegistry()
    with pytest.raises(ValueError, match="max_concurrency"):
        registry.register(AgentRuntime("bad", ("llm",), ("none",), 0))


def test_agent_runtime_select_respects_tools_and_concurrency():
    registry = AgentRuntimeRegistry()
    registry.register(AgentRuntime("researcher", ("tool_use",), ("mcp.search",), 1))
    registry.register(AgentRuntime("analyst", ("tool_use",), ("none",), 2))

    selected = registry.select("tool_use", tools=["mcp.search"])
    assert selected.name == "researcher"
    assert registry.select("tool_use", tools=["mcp.search"], active_loads={"researcher": 1}) is None


def test_agent_message_bus_validates_and_consumes_once():
    bus = AgentMessageBus()
    message = {"type": "result", "correlation_id": "run-1", "payload": {"ok": True}}
    bus.publish("researcher", "analyst", message)

    assert bus.consume("analyst")[0]["sender"] == "researcher"
    assert bus.consume("analyst") == []
    with pytest.raises(ValueError, match="invalid agent message"):
        bus.publish("researcher", "analyst", {"type": "bad"})


def test_agent_context_isolation():
    agent = AgentRuntime("tool", ("tool_use",), ("filesystem",), memory_scope="private")
    state = {
        "messages": [{"role": "user", "content": "objective"}],
        "subtasks": [{"id": "task-0", "agent": "tool"}, {"id": "task-1", "agent": "other"}],
        "subtask_results": [{"subtask_id": "task-0", "agent": "tool"}, {"subtask_id": "task-1", "agent": "other"}],
    }
    context = isolate_agent_context(agent, state)

    assert context["memory_scope"] == "private"
    assert [task["id"] for task in context["subtasks"]] == ["task-0"]
    assert [result["subtask_id"] for result in context["subtask_results"]] == ["task-0"]


def test_legacy_capability_registry_is_importable():
    registry = AgentRuntimeRegistry.legacy({
        "executor": {"task_types": ["llm"], "allowed_tools": ["none"], "max_concurrency": 4},
    })

    assert registry.get("executor").can_run("llm", ("none",)) is True
