from dataclasses import dataclass


@dataclass(frozen=True)
class AgentEvalCase:
    name: str
    input: str
    intent_response: str
    plan_response: str
    expected_agent_history: tuple[str, ...]
    expected_status: str
    critic_response: str = "success"


def production_agent_cases() -> list[AgentEvalCase]:
    return [
        AgentEvalCase(
            name="supervisor_routes_chat",
            input="hello",
            intent_response="chat",
            plan_response='[{"action":"reply","tool":"none"}]',
            expected_agent_history=("supervisor", "executor"),
            expected_status="success",
        ),
        AgentEvalCase(
            name="supervisor_routes_memory",
            input="remember this",
            intent_response="memory_write",
            plan_response="[]",
            expected_agent_history=("supervisor",),
            expected_status="success",
        ),
        AgentEvalCase(
            name="tool_flow_attributes_specialist",
            input="echo hello",
            intent_response="tool_use",
            plan_response='[{"action":"hello","tool":"echo","arguments":{"text":"hello"}}]',
            expected_agent_history=("supervisor", "executor", "tool"),
            expected_status="success",
        ),
    ]
