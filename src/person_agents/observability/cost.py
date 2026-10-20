from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class CostEntry:
    # 一条调用级成本样本；total_tokens 是 prompt + completion。
    timestamp: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


@dataclass
class CostTracker:
    session_id: str
    entries: list[CostEntry] = field(default_factory=list)

    def record(self, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: int) -> None:
        # 只在内存中聚合；生产限流使用 RateLimiter 持久化用量。
        self.entries.append(
            CostEntry(
                timestamp=datetime.now(UTC).isoformat(),
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
            )
        )

    @property
    def total_tokens(self) -> int:
        # 供 eval/chat 展示累计消耗。
        return sum(e.prompt_tokens + e.completion_tokens for e in self.entries)

    @property
    def total_calls(self) -> int:
        return len(self.entries)
