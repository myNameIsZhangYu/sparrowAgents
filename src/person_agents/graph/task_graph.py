import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


def merge_subtask_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for result in results:
        subtask_id = result.get("subtask_id", "")
        if subtask_id and subtask_id not in merged:
            merged[subtask_id] = result
    return list(merged.values())


def normalize_plan_to_task_graph(plan: list[dict[str, Any]], objective: str) -> list[dict[str, Any]]:
    subtasks: list[dict[str, Any]] = []
    previous_id: str | None = None
    for index, step in enumerate(plan):
        task_id = f"task-{index}"
        subtasks.append({
            "id": task_id,
            "action": step.get("action", ""),
            "tool": step.get("tool", "none"),
            "arguments": step.get("arguments", {}),
            "agent": "tool" if step.get("tool", "none") != "none" else "executor",
            "status": "pending",
            "depends_on": step.get("depends_on", [previous_id] if previous_id else []),
            "attempt": 0,
            "objective": objective,
        })
        previous_id = task_id
    return subtasks


@dataclass
class AgentEnvelope:
    from_agent: str
    to_agent: str
    type: str
    task_id: str
    correlation_id: str
    payload: dict[str, Any] = field(default_factory=dict)


class AgentMessageBus:
    def __init__(self) -> None:
        self._queues: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def publish(self, from_agent: str, to_agent: str, message: dict[str, Any]) -> None:
        self._queues[to_agent].append({**message, "from_agent": from_agent})

    async def publish_envelope(self, envelope: AgentEnvelope) -> None:
        if not validate_agent_envelope(envelope):
            raise ValueError("invalid agent envelope")
        self._queues[envelope.to_agent].append(envelope)

    async def consume(self, agent: str) -> list[dict[str, Any]]:
        messages = self._queues[agent]
        self._queues[agent] = []
        return messages


def validate_agent_envelope(envelope: AgentEnvelope) -> bool:
    return all(
        getattr(envelope, field_name)
        for field_name in ("from_agent", "to_agent", "type", "task_id", "correlation_id")
    )


def arbitrate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        raise ValueError("cannot arbitrate empty result list")

    def rank(result: dict[str, Any]) -> tuple[int, float]:
        status_weight = {"completed": 2, "failed": 1, "skipped": 0}.get(result.get("status", ""), 0)
        return (status_weight, float(result.get("confidence", 0.0)))

    return max(results, key=rank)


def merge_subtask_results_with_arbitration(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        grouped.setdefault(result.get("subtask_id", ""), []).append(result)
    return [arbitrate_results(group) for group in grouped.values()]


def retry_failed_subtasks(subtasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**task, "attempt": task.get("attempt", 0) + 1, "status": "pending"}
        if task.get("status") == "failed"
        else task
        for task in subtasks
    ]


FAILURE_TYPE_PREFIXES = (
    ("permission", "permission"),
    ("rejected", "permission"),
    ("not allowed", "permission"),
    ("timeout", "timeout"),
    ("timed out", "timeout"),
    ("invalid", "parameters"),
    ("validation", "parameters"),
    ("unknown tool", "tool"),
    ("dependency failed", "dependency_failure"),
    ("model", "model_output"),
)


def classify_failure_type(result: dict[str, Any] | None) -> str:
    if result is None or result.get("status") == "completed":
        return "unknown"
    if result.get("status") == "skipped":
        return "dependency_failure"
    error = f"{result.get('error') or ''} {result.get('output') or ''}".lower()
    for prefix, failure_type in FAILURE_TYPE_PREFIXES:
        if prefix in error:
            return failure_type
    return "tool" if error.strip() else "unknown"


def build_failure_analysis(subtasks: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for result in results:
        if result.get("subtask_id"):
            latest[result["subtask_id"]] = result
    return [
        {
            "subtask_id": task["id"],
            "failure_type": classify_failure_type(latest.get(task["id"])),
            "reason": (latest.get(task["id"]) or {}).get("error") or (latest.get(task["id"]) or {}).get("output") or "unknown failure",
            "attempt": task.get("attempt", 0),
        }
        for task in subtasks
        if task.get("status") == "failed"
    ]


def negotiate_resource_conflict(candidates: list[dict[str, Any]], *, resource: str) -> str:
    return max(candidates, key=lambda task: task.get("priority", 0))["subtask_id"]


class AgentCapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, dict[str, Any]] = {}

    def register(
        self,
        agent: str,
        *,
        task_types: list[str],
        allowed_tools: list[str],
        max_concurrency: int = 1,
    ) -> None:
        self._capabilities[agent] = {
            "task_types": task_types,
            "allowed_tools": allowed_tools,
            "max_concurrency": max_concurrency,
        }

    def select(
        self,
        task_type: str,
        *,
        allowed_tools: list[str] | None = None,
        active_loads: dict[str, int] | None = None,
    ) -> str | None:
        for agent, capability in self._capabilities.items():
            if task_type not in capability["task_types"]:
                continue
            if allowed_tools and any(tool not in capability["allowed_tools"] for tool in allowed_tools):
                continue
            loads = active_loads or {}
            if loads.get(agent, 0) >= capability["max_concurrency"]:
                continue
            return agent
        return None


def register_agent_capability(
    registry: AgentCapabilityRegistry,
    agent: str,
    *,
    task_types: list[str],
    allowed_tools: list[str],
    max_concurrency: int = 1,
) -> None:
    registry.register(
        agent,
        task_types=task_types,
        allowed_tools=allowed_tools,
        max_concurrency=max_concurrency,
    )


class SharedWorkspaceLock:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._owners: dict[str, str] = {}

    def is_locked(self, resource: str) -> bool:
        return self._locks[resource].locked()

    def acquire(self, resource: str, *, owner: str):
        class _Context:
            async def __aenter__(self):
                await self._lock.acquire()
                self._owners[resource] = owner

            async def __aexit__(self, exc_type, exc, tb):
                self._lock.release()

        context = _Context()
        context._lock = self._locks[resource]
        context._owners = self._owners
        return context


class SharedWorkspaceBlackboard:
    def __init__(self) -> None:
        self._boards: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def update(self, workspace: str, agent: str, payload: dict[str, Any]) -> None:
        self._boards[workspace].append({**payload, "agent": agent})

    async def read(self, workspace: str, *, for_agent: str) -> list[dict[str, Any]]:
        return [entry for entry in self._boards[workspace] if entry["agent"] == for_agent]


def build_agent_context(agent: str, state: dict[str, Any]) -> dict[str, Any]:
    agents_by_id = {
        task["id"]: task.get("agent")
        for task in state.get("subtasks", [])
    }
    subtask_results = [
        result
        for result in state.get("subtask_results", [])
        if result.get("agent") == agent or agents_by_id.get(result.get("subtask_id")) == agent
    ]
    return {
        "objective": state.get("messages", [{}])[-1].get("content", ""),
        "subtasks": state.get("subtasks", []),
        "subtask_results": subtask_results,
    }


async def execute_task_graph(
    subtasks: list[dict[str, Any]],
    runner,
    max_concurrency: int = 4,
) -> list[dict[str, Any]]:
    by_id = {task["id"]: task for task in subtasks}
    pending = {task["id"] for task in subtasks}
    statuses: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    external_statuses = {
        task["id"]: task.get("status") for task in subtasks if task.get("status") in {"completed", "failed", "skipped"}
    }
    statuses.update(external_statuses)
    while True:
        changed = False
        for task_id in list(pending):
            task = by_id[task_id]
            if any(statuses.get(dep) == "skipped" for dep in task.get("depends_on", [])):
                statuses[task_id] = "skipped"
                results.append({"subtask_id": task_id, "status": "skipped", "output": "", "error": "dependency skipped"})
                pending.remove(task_id)
                changed = True
        if not changed:
            break
    semaphore = asyncio.Semaphore(max_concurrency)

    async def run_one(task: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                allowed_tools = task.get("allowed_tools")
                if task.get("tool", "none") != "none" and allowed_tools is not None and task["tool"] not in allowed_tools:
                    raise PermissionError(f"tool {task['tool']} is not allowed for agent {task.get('agent', 'unknown')}")
                result = await runner(task)
            except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
                failure = {"subtask_id": task["id"], "status": "failed", "output": "", "error": str(exc)}
                results.append(failure)
                statuses[task["id"]] = "failed"
                return failure
            result = {
                **result,
                "subtask_id": task["id"],
                "status": "failed" if result.get("degraded") else "completed",
                "error": result.get("error"),
            }
            results.append(result)
            statuses[task["id"]] = result["status"]
            return result

    while pending:
        runnable = []
        for task_id in list(pending):
            task = by_id[task_id]
            dependencies = task.get("depends_on", [])
            if any(statuses.get(dep) == "failed" for dep in dependencies):
                statuses[task_id] = "skipped"
                results.append({"subtask_id": task_id, "status": "skipped", "output": "", "error": "dependency failed"})
                pending.remove(task_id)
                continue
            if any(statuses.get(dep) == "skipped" for dep in dependencies):
                statuses[task_id] = "skipped"
                results.append({"subtask_id": task_id, "status": "skipped", "output": "", "error": "dependency skipped"})
                pending.remove(task_id)
                continue
            if any(dep not in statuses and dep in pending for dep in dependencies):
                continue
            runnable.append(task)
        if not runnable:
            break
        await asyncio.gather(*(run_one(task) for task in runnable))
        for task in runnable:
            pending.discard(task["id"])

    return results
