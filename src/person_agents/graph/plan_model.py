from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RISK_LEVELS = {"low", "medium", "high"}


@dataclass
class PlanStep:
    id: str
    action: str
    tool: str = "none"
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    milestone_id: str | None = None
    risk: str = "medium"
    timeout_s: int | None = None
    agent: str | None = None
    status: str = "pending"


@dataclass
class PlanModel:
    objective: str
    steps: list[PlanStep]
    milestones: list[dict[str, Any]] = field(default_factory=list)
    budget: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw_plan: Any, *, objective: str) -> PlanModel:
        payload = raw_plan if isinstance(raw_plan, dict) else {"steps": raw_plan}
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raw_steps = [{"action": objective, "tool": "none", "depends_on": []}]

        steps: list[PlanStep] = []
        previous_id: str | None = None
        for index, raw_step in enumerate(raw_steps):
            step_data = raw_step if isinstance(raw_step, dict) else {"action": str(raw_step)}
            step_id = str(step_data.get("id") or f"task-{index}")
            depends_on = step_data.get("depends_on")
            if not isinstance(depends_on, list):
                depends_on = [previous_id] if previous_id else []
            else:
                depends_on = [str(dep) for dep in depends_on if dep]
            risk = str(step_data.get("risk") or "medium").lower()
            if risk not in RISK_LEVELS:
                risk = "medium"
            timeout_s = step_data.get("timeout_s")
            steps.append(PlanStep(
                id=step_id,
                action=str(step_data.get("action") or objective),
                tool=str(step_data.get("tool") or "none"),
                arguments=step_data.get("arguments") if isinstance(step_data.get("arguments"), dict) else {},
                depends_on=depends_on,
                milestone_id=str(step_data["milestone_id"]) if step_data.get("milestone_id") else None,
                risk=risk,
                timeout_s=int(timeout_s) if isinstance(timeout_s, (int, float)) and timeout_s > 0 else None,
                agent=str(step_data["agent"]) if step_data.get("agent") else None,
                status=str(step_data.get("status") or "pending"),
            ))
            previous_id = step_id

        model = cls(
            objective=str(payload.get("objective") or objective),
            steps=steps,
            milestones=payload.get("milestones") if isinstance(payload.get("milestones"), list) else [],
            budget=payload.get("budget") if isinstance(payload.get("budget"), dict) else {},
        )
        model._validate()
        return model

    def _validate(self) -> None:
        ids = {step.id for step in self.steps}
        for step in self.steps:
            step.depends_on = [dep for dep in step.depends_on if dep in ids and dep != step.id]

        # Kahn's algorithm gives deterministic cycle detection without recursion.
        remaining = {step.id: len(step.depends_on) for step in self.steps}
        dependents = {step.id: [] for step in self.steps}
        for step in self.steps:
            for dependency in step.depends_on:
                dependents[dependency].append(step.id)
        ready = [step_id for step_id, count in remaining.items() if count == 0]
        completed = 0
        while ready:
            step_id = ready.pop()
            completed += 1
            for dependent in dependents[step_id]:
                remaining[dependent] -= 1
                if remaining[dependent] == 0:
                    ready.append(dependent)
        if completed != len(self.steps):
            raise ValueError("plan dependency cycle")

    def to_legacy(self) -> list[dict[str, Any]]:
        return [{
            "id": step.id,
            "action": step.action,
            "tool": step.tool,
            "arguments": step.arguments,
            "depends_on": list(step.depends_on),
            "milestone_id": step.milestone_id,
            "risk": step.risk,
            "timeout_s": step.timeout_s,
            "agent": step.agent,
            "status": step.status,
        } for step in self.steps]
