import asyncio
import json

import pytest

from person_agents.eval.agent_cases import AgentEvalCase, production_agent_cases
from person_agents.eval.runner import EvalRunner


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.degraded = False
        self.model = "fake"
        self.prompt_tokens = 1
        self.completion_tokens = 1


def test_agent_eval_cases_cover_production_paths():
    cases = production_agent_cases()

    assert {case.name for case in cases} == {
        "supervisor_routes_chat",
        "supervisor_routes_memory",
        "tool_flow_attributes_specialist",
    }
    assert all(isinstance(case, AgentEvalCase) for case in cases)
    assert all(case.expected_agent_history[0] == "supervisor" for case in cases)


async def test_eval_runner_exposes_agent_case_contract(monkeypatch):
    from person_agents.security.context import RequestContext

    runner = EvalRunner()
    case = production_agent_cases()[0]
    state = {
        "agent_history": list(case.expected_agent_history),
        "status": case.expected_status,
        "tool_results": [{"output": "ok"}],
    }
    captured = {}

    async def fake_run_graph(prompt, session_id, context, **kwargs):
        captured["prompt"] = prompt
        captured["session_id"] = session_id
        captured["context"] = context
        return state

    monkeypatch.setattr("person_agents.graph.engine.run_graph", fake_run_graph)

    report = await runner.run_agent_cases([case])

    assert captured["prompt"] == case.input
    assert isinstance(captured["context"], RequestContext)
    assert report["total"] == 1
    assert report["results"][0]["passed"] is True


def test_real_cli_eval_report_includes_runtime_metrics(monkeypatch):
    from person_agents.eval.cli_cases import run_real_cli_workflow_eval

    plan = [{"action": "answer", "tool": "none", "arguments": {}, "depends_on": []}]

    class Response:
        content = json.dumps(plan)
        degraded = False
        model = "fake"
        prompt_tokens = 7
        completion_tokens = 3

    async def fake_llm(messages):
        return Response()

    monkeypatch.setattr("person_agents.graph.nodes.call_with_fallback", fake_llm)

    report = asyncio.run(run_real_cli_workflow_eval(enable=True))

    assert report["pass_rate"] == 1.0
    assert report["tokens"] == 10
    assert report["latency_ms"] >= 0


def test_disabled_real_cli_eval_does_not_call_model(monkeypatch):
    from person_agents.eval.cli_cases import run_real_cli_workflow_eval

    async def fail_llm(messages):
        raise AssertionError("disabled eval must not call the model")

    monkeypatch.setattr("person_agents.graph.nodes.call_with_fallback", fail_llm)

    report = asyncio.run(run_real_cli_workflow_eval(enable=False, threshold=0.8))

    assert report["enabled"] is False
    assert report["passed"] is True


@pytest.mark.skipif(True, reason="Full graph execution is validated with RUN_LANGGRAPH_E2E outside this sandbox")
async def test_agent_eval_cases_cover_production_paths_end_to_end():
    raise AssertionError("Replace with external graph E2E harness")
