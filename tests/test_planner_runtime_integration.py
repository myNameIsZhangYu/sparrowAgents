import shutil

from person_agents.skills.skill_store import SkillStore


async def test_planner_injects_ranked_skills(monkeypatch):
    from person_agents.graph.nodes import planner_node

    class Response:
        content = "[]"
        degraded = False

    async def response_call(messages):
        captured.extend(messages)
        return Response()

    skill_root = __import__("pathlib").Path("/tmp") / "skills-test"
    shutil.rmtree(skill_root, ignore_errors=True)
    store = SkillStore(skill_root)
    store.save({
        "name": "write-python-file",
        "version": "1.0.0",
        "description": "Create a Python file and verify it",
        "plan": [{"action": "write", "tool": "filesystem"}],
        "tools": ["filesystem"],
    })
    captured = []
    monkeypatch.setattr("person_agents.graph.nodes.call_with_fallback", response_call)

    result = await planner_node({
        "messages": [{"role": "user", "content": "Create a Python file"}],
        "memory_context": [],
        "tool_results": [],
        "session_id": "test",
    }, services=type("Services", (), {"skill_store": store, "registry": None})())

    system_prompt = captured[0]["content"]
    assert "Matching skills" in system_prompt
    assert "write-python-file" in system_prompt
    assert result["plan"][0]["action"] == "Create a Python file"
