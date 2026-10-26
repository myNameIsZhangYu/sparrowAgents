from person_agents.graph.nodes import critic_node


class Response:
    content = "unexpected"
    degraded = False
    model = "fake"
    prompt_tokens = 0
    completion_tokens = 0


async def test_critic_uses_structured_success_without_llm(monkeypatch):
    async def fail_llm(*args, **kwargs):
        raise AssertionError("structured success must not call the LLM")

    monkeypatch.setattr("person_agents.graph.nodes.governed_llm_call", fail_llm)
    result = await critic_node({
        "messages": [{"role": "user", "content": "task"}],
        "tool_results": [{"output": "ok"}],
        "subtask_results": [{"subtask_id": "task-0", "status": "completed", "degraded": False, "error": None}],
        "subtasks": [{"id": "task-0", "status": "completed"}],
        "retry_count": 0,
        "started_at": None,
        "owner_user_id": "user",
        "tenant_id": "tenant",
        "session_id": "session",
    })
    assert result["critic_feedback"] == "success"
    assert result["status"] == "success"
