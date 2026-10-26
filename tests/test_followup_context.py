import json

from person_agents.graph.nodes import planner_node


class Response:
    content = '[{"action":"answer","tool":"none"}]'
    degraded = False
    model = "fake"
    prompt_tokens = 0
    completion_tokens = 0


async def test_planner_receives_prior_file_evidence(monkeypatch):
    captured = {}

    async def fake_llm(messages):
        captured["messages"] = messages
        return Response()

    monkeypatch.setattr("person_agents.graph.nodes.call_with_fallback", fake_llm)
    state = {
        "messages": [{"role": "user", "content": "刚才创建了什么文件？"}],
        "plan": [],
        "current_step": 0,
        "tool_results": [{"step": 0, "output": {"status": "ok", "path": "first.txt", "operation": "write"}}],
        "memory_context": [],
        "registry": None,
    }
    await planner_node(state)
    prompt = json.dumps(captured["messages"], ensure_ascii=False)
    assert "first.txt" in prompt
