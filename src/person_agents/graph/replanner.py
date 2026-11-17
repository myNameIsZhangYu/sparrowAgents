from __future__ import annotations

REPAIR_ACTIONS = {
    "parameters": "repair_parameters",
    "tool": "replace_tool",
    "permission": "abandon",
    "timeout": "increase_timeout",
    "model_output": "regenerate",
    "dependency_failure": "retry_dependency",
}


def choose_repair_action(failure_type: str, *, attempt: int, max_attempts: int = 3) -> dict[str, str]:
    if attempt >= max_attempts:
        return {"action": "abandon", "reason": "repair attempt limit reached"}
    action = REPAIR_ACTIONS.get(failure_type, "retry")
    return {"action": action, "reason": f"repair {failure_type}"}
