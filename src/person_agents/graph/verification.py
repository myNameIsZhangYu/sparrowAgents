from __future__ import annotations

from typing import Any


def verify_subtask_evidence(result: dict[str, Any]) -> bool:
    verification = result.get("verification")
    return isinstance(verification, dict) and verification.get("passed") is True
