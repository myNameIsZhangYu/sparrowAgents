import asyncio

from person_agents.cli import run_agent_with_progress


async def test_run_agent_with_progress_emits_live_and_final_messages():
    events = []

    async def agent():
        await asyncio.sleep(0)
        return "done"

    result = await run_agent_with_progress(
        agent,
        on_progress=events.append,
        message="working",
        final_message="completed",
    )

    assert result == "done"
    assert events == ["working", "completed"]
