from person_agents.observability.metrics import registry


def get_sample(name, labels):
    return registry.get_sample_value(name, labels)


async def test_agent_metrics_count_supervisor_completion():
    from person_agents.graph.engine import supervisor_node

    before = get_sample("agent_runs_total", {"agent": "supervisor", "status": "success"})
    await supervisor_node({"intent": "chat"})

    after = get_sample("agent_runs_total", {"agent": "supervisor", "status": "success"})
    assert (after or 0) == (before or 0) + 1


async def test_agent_metrics_record_tool_failure(monkeypatch):
    from person_agents.graph.engine import tool_specialist

    class Registry:
        def get(self, name):
            return None

    before = get_sample("agent_runs_total", {"agent": "tool", "status": "failed"})
    await tool_specialist({"current_step": 0, "plan": [{"tool": "missing"}]}, registry=Registry())

    after = get_sample("agent_runs_total", {"agent": "tool", "status": "failed"})
    assert (after or 0) == (before or 0) + 1


async def test_agent_metrics_observe_tool_duration():

    class Tool:
        def __init__(self):
            self.parameters = {"type": "object"}

        name = "echo"
        description = "echo"
        requires_confirm = False

        async def handler(self, arguments):
            return arguments

    class Registry:
        def get(self, name):
            return Tool() if name == "echo" else None

    async def successful_tool_call(tool, arguments, handler, **kwargs):
        return await handler(arguments)

    import pytest

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("person_agents.graph.engine.governed_tool_call", successful_tool_call)
    try:
        await _run_case(Registry())
    finally:
        monkeypatch.undo()


async def _run_case(registry):
    from person_agents.graph.engine import tool_specialist

    state = {
        "current_step": 0,
        "plan": [{"action": "hello", "tool": "echo", "arguments": {"text": "hello"}}],
    }
    before = get_sample("agent_run_duration_seconds_count", {"agent": "tool"})
    await tool_specialist(state, registry=registry)

    after = get_sample("agent_run_duration_seconds_count", {"agent": "tool"})
    assert (after or 0) == (before or 0) + 1
