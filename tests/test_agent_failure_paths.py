

async def test_tool_specialist_failure_degrades_without_losing_partial_results():
    from person_agents.graph.engine import tool_specialist

    class Registry:
        def get(self, name):
            return None

    state = {
        "current_step": 0,
        "plan": [{"action": "hello", "tool": "broken"}],
        "tool_results": [{"step": -1, "output": "partial"}],
        "agent_history": ["supervisor", "executor"],
    }

    result = await tool_specialist(state, registry=Registry())

    assert result["degraded"] is True
    assert result["status"] == "failed"
    assert result["error"] == "Unknown tool: broken"
    assert result["tool_results"][0]["output"] == "partial"
    assert result["tool_results"][-1]["output"] == "Unknown tool: broken"
    assert result["current_agent"] == "tool"
