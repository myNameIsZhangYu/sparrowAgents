import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from person_agents.runtime.state import InMemoryStateStore, StateStore

logger = structlog.get_logger()


class QuotaExceededError(RuntimeError):
    # 调用方据此把配额失败转换为 HTTP 429。
    pass


@dataclass
class TokenBucket:
    capacity: int
    refill_rate: float
    tokens: float = 0
    last_refill: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.tokens == 0:
            self.tokens = self.capacity

    def try_acquire(self) -> bool:
        # 按时间差平滑补充 token，再判断是否能取走一个。
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class RateLimiter:
    def __init__(
        self,
        state_store: StateStore | None = None,
        llm_capacity: int = 10,
        llm_refill_rate: float = 1.0,
        tool_capacity: int = 20,
        tool_refill_rate: float = 2.0,
        max_running_runs: int = 3,
        daily_run_limit: int = 1000,
    ):
        # 限流器默认进程内隔离；中间件可显式注入共享存储用于分布式部署。
        self.state_store = state_store or InMemoryStateStore()
        self._llm_capacity = llm_capacity
        self._llm_refill_rate = llm_refill_rate
        self._tool_capacity = tool_capacity
        self._tool_refill_rate = tool_refill_rate
        self._max_running_runs = max_running_runs
        self._daily_run_limit = daily_run_limit

    @staticmethod
    def _day_key(prefix: str, tenant_id: str, owner_user_id: str) -> str:
        today = datetime.now(UTC).strftime("%Y%m%d")
        return f"{prefix}:{today}:{tenant_id}:{owner_user_id}"

    def acquire_run_slot(self, owner_user_id: str = "local", tenant_id: str = "default") -> str:
        # 每日配额和并发槽位分开检查；键包含租户与用户，避免互相影响。
        daily_key = self._day_key("run_daily", tenant_id, owner_user_id)
        current = self.state_store.get_json(daily_key) or 0
        if current >= self._daily_run_limit:
            raise QuotaExceededError("Daily run quota exceeded")
        slot_key = f"run_slot:{tenant_id}:{owner_user_id}"
        slots = self.state_store.get_json(slot_key) or {}
        if len(slots) >= self._max_running_runs:
            raise QuotaExceededError("Concurrent run quota exceeded")
        slot_id = uuid.uuid4().hex
        slots[slot_id] = time.time()
        self.state_store.set_json(slot_key, slots, ttl_seconds=86400)
        self.state_store.set_json(daily_key, current + 1, ttl_seconds=172800)
        return slot_id

    def release_run_slot(self, slot_id: str, owner_user_id: str = "local", tenant_id: str = "default") -> None:
        # 请求结束必须释放槽位，否则异常路径会永久占用并发额度。
        slot_key = f"run_slot:{tenant_id}:{owner_user_id}"
        slots = self.state_store.get_json(slot_key) or {}
        slots.pop(slot_id, None)
        self.state_store.set_json(slot_key, slots, ttl_seconds=86400)

    def allow_run(self, owner_user_id: str = "local", tenant_id: str = "default") -> str:
        return self.acquire_run_slot(owner_user_id, tenant_id)

    def record_usage(self, owner_user_id: str = "local", tenant_id: str = "default", tokens: int = 0, cost: float = 0.0) -> None:
        # token 和 cost 分开累计，便于按不同上限告警。
        if tokens:
            key = self._day_key("token_daily", tenant_id, owner_user_id)
            self.state_store.set_json(key, (self.state_store.get_json(key) or 0) + tokens, ttl_seconds=172800)
        if cost:
            key = self._day_key("cost_daily", tenant_id, owner_user_id)
            self.state_store.set_json(key, (self.state_store.get_json(key) or 0.0) + cost, ttl_seconds=172800)

    def record_llm_usage(self, response: object, owner_user_id: str = "local", tenant_id: str = "default") -> tuple[int, float]:
        # provider 未返回 token 时按约 4 字符/token 估算。
        tokens = response.prompt_tokens + response.completion_tokens
        estimated_tokens = tokens or (len(str(getattr(response, "content", ""))) + 3) // 4
        cost = estimated_tokens * 0.000002
        self.record_usage(owner_user_id, tenant_id, estimated_tokens, cost)
        self.check_usage(owner_user_id, tenant_id)
        return estimated_tokens, cost

    def check_usage(self, owner_user_id: str = "local", tenant_id: str = "default") -> None:
        from person_agents.config.settings import settings

        # 每次记录后立即检查，保证接近预算时尽快拒绝后续请求。
        tokens = self.state_store.get_json(self._day_key("token_daily", tenant_id, owner_user_id)) or 0
        cost = self.state_store.get_json(self._day_key("cost_daily", tenant_id, owner_user_id)) or 0.0
        if tokens > settings.tenant_daily_token_limit:
            raise QuotaExceededError("Daily token quota exceeded")
        if cost > settings.tenant_daily_cost_limit:
            raise QuotaExceededError("Daily cost quota exceeded")
    def allow_llm(self, owner_user_id: str = "local", tenant_id: str = "default") -> bool:
        # Redis 可用时为分布式限流；内存态仅适合单实例。
        return self.state_store.acquire_bucket(f"llm:{tenant_id}:{owner_user_id}", self._llm_capacity, self._llm_refill_rate)

    def allow_tool(self, owner_user_id: str = "local", tenant_id: str = "default") -> bool:
        # 当前工具桶在 worker 进程内，用于限制单进程工具调用频率。
        if not hasattr(self, "_tool_buckets"):
            self._tool_buckets = {}
        key = f"{tenant_id}:{owner_user_id}"
        if key not in self._tool_buckets:
            self._tool_buckets[key] = TokenBucket(self._tool_capacity, self._tool_refill_rate)
        return self._tool_buckets[key].try_acquire()
