from __future__ import annotations

import time


class BudgetController:
    def __init__(
        self,
        *,
        max_tokens: int | None = None,
        max_cost_usd: float | None = None,
        max_seconds: float | None = None,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_cost_usd = max_cost_usd
        self.max_seconds = max_seconds
        self._started_at = time.monotonic()
        self.usage = {"tokens": 0, "cost_usd": 0.0, "seconds": 0.0}

    def consume(self, *, tokens: int = 0, cost_usd: float = 0.0) -> None:
        self.usage["tokens"] += tokens
        self.usage["cost_usd"] += cost_usd
        self.usage["seconds"] = time.monotonic() - self._started_at

    def exhausted_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.max_tokens is not None and self.usage["tokens"] > self.max_tokens:
            reasons.append("tokens")
        if self.max_cost_usd is not None and self.usage["cost_usd"] > self.max_cost_usd:
            reasons.append("cost_usd")
        if self.max_seconds is not None and self.usage["seconds"] > self.max_seconds:
            reasons.append("seconds")
        return reasons

    def exhausted(self) -> bool:
        return bool(self.exhausted_reasons())

    @property
    def critic_feedback(self) -> str:
        return f"fail: budget exhausted ({', '.join(self.exhausted_reasons())})"
