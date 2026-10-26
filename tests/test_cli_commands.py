from person_agents.cli import _complete_pending_tool


def test_exit_command_is_reserved():
    from person_agents.cli import EXIT_COMMAND

    assert EXIT_COMMAND == "/exit"


async def test_confirmation_generic_error_is_rendered(monkeypatch):
    async def fail_confirm(*args, **kwargs):
        raise RuntimeError("state store unavailable")

    monkeypatch.setattr("person_agents.graph.tool_confirmation.confirm_pending_tool", fail_confirm)

    result = await _complete_pending_tool("token", "session")

    assert result == "确认失败：state store unavailable"
