from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class AgentRuntime:
    name: str
    task_types: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    max_concurrency: int = 1
    persona: str = ""
    skills: tuple[str, ...] = ()
    memory_scope: str = ""
    state: dict[str, Any] = field(default_factory=dict)

    def can_run(self, task_type: str, tools: tuple[str, ...]) -> bool:
        if task_type not in self.task_types:
            return False
        return all(tool in self.allowed_tools for tool in tools)

    def with_state(self, **updates: Any) -> AgentRuntime:
        return replace(self, state={**self.state, **updates})


class AgentRuntimeRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, AgentRuntime] = {}

    def register(self, agent: AgentRuntime) -> None:
        if agent.max_concurrency < 1:
            raise ValueError("agent max_concurrency must be positive")
        if not agent.task_types or not agent.allowed_tools:
            raise ValueError("agent task_types and allowed_tools cannot be empty")
        self._agents[agent.name] = agent

    def get(self, name: str) -> AgentRuntime | None:
        return self._agents.get(name)

    def select(
        self,
        task_type: str,
        *,
        tools: tuple[str, ...] | list[str] = (),
        active_loads: dict[str, int] | None = None,
    ) -> AgentRuntime | None:
        tools = tuple(tools)
        loads = active_loads or {}
        for agent in self._agents.values():
            if agent.can_run(task_type, tools) and loads.get(agent.name, 0) < agent.max_concurrency:
                return agent
        return None

    def to_config(self) -> list[dict[str, Any]]:
        return [{
            "name": agent.name,
            "task_types": list(agent.task_types),
            "allowed_tools": list(agent.allowed_tools),
            "max_concurrency": agent.max_concurrency,
            "persona": agent.persona,
            "skills": list(agent.skills),
            "memory_scope": agent.memory_scope,
            "state": dict(agent.state),
        } for agent in self._agents.values()]

    @classmethod
    def from_config(cls, config: list[dict[str, Any]]) -> AgentRuntimeRegistry:
        registry = cls()
        for item in config:
            registry.register(AgentRuntime(
                name=str(item["name"]),
                task_types=tuple(item.get("task_types", [])),
                allowed_tools=tuple(item.get("allowed_tools", [])),
                max_concurrency=int(item.get("max_concurrency", 1)),
                persona=str(item.get("persona", "")),
                skills=tuple(item.get("skills", [])),
                memory_scope=str(item.get("memory_scope", "")),
                state=dict(item.get("state", {})),
            ))
        return registry

    @classmethod
    def legacy(cls, capabilities: dict[str, dict[str, Any]]) -> AgentRuntimeRegistry:
        registry = cls()
        for name, capability in capabilities.items():
            registry.register(AgentRuntime(
                name=name,
                task_types=tuple(capability.get("task_types", [])),
                allowed_tools=tuple(capability.get("allowed_tools", [])),
                max_concurrency=int(capability.get("max_concurrency", 1)),
            ))
        return registry


class AgentMessageBus:
    def __init__(self) -> None:
        self._messages: dict[str, list[dict[str, Any]]] = {}

    def publish(self, sender: str, recipient: str, message: dict[str, Any]) -> None:
        if not sender or not recipient or "type" not in message or "correlation_id" not in message:
            raise ValueError("invalid agent message")
        self._messages.setdefault(recipient, []).append({**message, "sender": sender})

    def consume(self, agent: str) -> list[dict[str, Any]]:
        messages = self._messages.get(agent, [])
        self._messages[agent] = []
        return messages


def isolate_agent_context(agent: AgentRuntime, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "objective": state.get("messages", [{}])[-1].get("content", ""),
        "agent": agent.name,
        "persona": agent.persona,
        "skills": list(agent.skills),
        "memory_scope": agent.memory_scope or agent.name,
        "state": dict(agent.state),
        "subtasks": [task for task in state.get("subtasks", []) if task.get("agent") == agent.name],
        "subtask_results": [result for result in state.get("subtask_results", []) if result.get("agent") == agent.name],
    }
