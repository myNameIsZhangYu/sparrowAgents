import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass
class Confirmation:
    token: str
    owner_user_id: str
    tenant_id: str
    tool_name: str
    arguments: dict[str, Any]
    expires_at: float


class StateStore(Protocol):
    # 状态存储协议：统一 JSON 状态、限流令牌桶和一次性确认令牌的读写接口。
    def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None: ...
    def get_json(self, key: str) -> Any | None: ...
    def delete(self, key: str) -> None: ...
    def acquire_bucket(self, key: str, capacity: int, refill_rate: float) -> bool: ...
    def store_confirmation(self, confirmation: Confirmation, ttl_seconds: int) -> Confirmation: ...
    def get_confirmation(self, token: str) -> Confirmation | None: ...
    def consume_confirmation(self, token: str) -> Confirmation | None: ...


class InMemoryStateStore:
    def __init__(self) -> None:
        # value 是 (data, expires_at)，TTL 过期后读取时惰性删除。
        self._values: dict[str, tuple[Any, float | None]] = {}

    @staticmethod
    def _bucket_state(value: tuple[Any, float | None]) -> tuple[Any, float | None]:
        # 保留原始桶状态结构，为不同后端扩展状态格式留出边界。
        return value

    def _expired(self, expires_at: float | None) -> bool:
        # 判断 TTL 是否已经超过当前墙钟时间。
        return expires_at is not None and expires_at < time.time()

    def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        # 写入数据并计算绝对过期时间；无 TTL 时永久保存在进程内存中。
        self._values[key] = (value, time.time() + ttl_seconds if ttl_seconds else None)

    def get_json(self, key: str) -> Any | None:
        # 读取时惰性清理过期项，避免引入后台清理线程。
        item = self._values.get(key)
        if item is None or self._expired(item[1]):
            self._values.pop(key, None)
            return None
        return item[0]

    def delete(self, key: str) -> None:
        # 显式删除键值，用于确认令牌消费等一次性状态。
        self._values.pop(key, None)

    def acquire_bucket(self, key: str, capacity: int, refill_rate: float) -> bool:
        # 令牌桶按单调时钟补充，避免系统时间回拨影响限流。
        now = time.monotonic()
        # 读取桶状态；首次访问时桶为满容量。
        state = self.get_json(f"bucket:{key}")
        tokens = float(capacity) if state is None else float(state["tokens"])
        last = now if state is None else float(state["last"])
        # 根据距离上次的间隔补充令牌，并限制在容量上限内。
        tokens = min(float(capacity), tokens + (now - last) * refill_rate)
        if tokens < 1:
            # 令牌不足时不放行，但仍持久化累计进度。
            self.set_json(f"bucket:{key}", {"tokens": tokens, "last": now})
            return False
        # 放行请求并扣除一个令牌。
        self.set_json(f"bucket:{key}", {"tokens": tokens - 1, "last": now})
        return True

    def store_confirmation(self, confirmation: Confirmation, ttl_seconds: int) -> Confirmation:
        # 确认令牌使用自身过期时间；若已到期则至少保留 1 秒，确保失败路径可见。
        ttl = max(1, int(confirmation.expires_at - time.time()))
        self.set_json(f"confirmation:{confirmation.token}", asdict(confirmation), ttl_seconds=ttl or ttl_seconds)
        return confirmation

    def get_confirmation(self, token: str) -> Confirmation | None:
        # 从 JSON 状态还原确认对象，并清理已经过期的令牌。
        value = self.get_json(f"confirmation:{token}")
        if value is None:
            return None
        confirmation = Confirmation(**value)
        if confirmation.expires_at < time.time():
            self.delete(f"confirmation:{token}")
            return None
        return confirmation

    def consume_confirmation(self, token: str) -> Confirmation | None:
        # 先读取校验，再删除；保证同一确认令牌只会被成功消费一次。
        confirmation = self.get_confirmation(token)
        if confirmation is None:
            return None
        self.delete(f"confirmation:{token}")
        return confirmation


class RedisStateStore:
    def __init__(self, client: Any) -> None:
        self._client = client

    def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        payload = json.dumps(value)
        if ttl_seconds:
            self._client.set(key, payload, ex=ttl_seconds)
        else:
            self._client.set(key, payload)

    def get_json(self, key: str) -> Any | None:
        value = self._client.get(key)
        return json.loads(value) if value is not None else None

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def acquire_bucket(self, key: str, capacity: int, refill_rate: float) -> bool:
        name = f"bucket:{key}"
        with self._client.pipeline() as pipe:
            try:
                # WATCH + multi 模拟原子令牌桶；Redis 不可用时显式失败。
                pipe.watch(name)
                value = pipe.get(name)
                if value is None:
                    tokens, last = float(capacity), time.monotonic()
                else:
                    state = json.loads(value)
                    tokens, last = float(state["tokens"]), float(state["last"])
                now = time.monotonic()
                tokens = min(float(capacity), tokens + (now - last) * refill_rate)
                if tokens < 1:
                    pipe.multi()
                    pipe.set(name, json.dumps({"tokens": tokens, "last": now}))
                    pipe.execute()
                    return False
                pipe.multi()
                pipe.set(name, json.dumps({"tokens": tokens - 1, "last": now}))
                pipe.execute()
                return True
            except Exception:
                pipe.unwatch()
                raise

    def store_confirmation(self, confirmation: Confirmation, ttl_seconds: int) -> Confirmation:
        self.set_json(f"confirmation:{confirmation.token}", asdict(confirmation), ttl_seconds=ttl_seconds)
        return confirmation

    def get_confirmation(self, token: str) -> Confirmation | None:
        value = self.get_json(f"confirmation:{token}")
        return Confirmation(**value) if value else None

    def consume_confirmation(self, token: str) -> Confirmation | None:
        name = f"confirmation:{token}"
        # GETDEL 保证确认令牌只能消费一次。
        value = self._client.getdel(name)
        if value is None:
            return None
        confirmation = Confirmation(**json.loads(value))
        if confirmation.expires_at < time.time():
            return None
        return confirmation


def create_state_store(redis_url: str = "") -> StateStore:
    if redis_url:
        # 生产应配置 Redis；未配置时退化为进程内状态。
        import redis

        return RedisStateStore(redis.Redis.from_url(redis_url, decode_responses=True))
    from person_agents.config.settings import settings

    return SQLiteStateStore(settings.state_db_path)


class SQLiteStateStore:
    """Durable local implementation of StateStore."""

    def __init__(self, db_path: Any):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS state_json (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL)"
        )
        self._conn.commit()

    def _clean(self) -> None:
        self._conn.execute("DELETE FROM state_json WHERE expires_at IS NOT NULL AND expires_at < ?", (time.time(),))
        self._conn.commit()

    def set_json(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds else None
        self._conn.execute(
            "INSERT INTO state_json (key, value, expires_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at",
            (key, json.dumps(value), expires_at),
        )
        self._conn.commit()

    def get_json(self, key: str) -> Any | None:
        self._clean()
        row = self._conn.execute("SELECT value, expires_at FROM state_json WHERE key=?", (key,)).fetchone()
        if not row or (row[1] is not None and row[1] < time.time()):
            return None
        return json.loads(row[0])

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM state_json WHERE key=?", (key,))
        self._conn.commit()

    def acquire_bucket(self, key: str, capacity: int, refill_rate: float) -> bool:
        state = self.get_json(f"bucket:{key}")
        now = time.monotonic()
        tokens = float(capacity) if state is None else float(state["tokens"])
        last = now if state is None else float(state["last"])
        tokens = min(float(capacity), tokens + (now - last) * refill_rate)
        if tokens < 1:
            self.set_json(f"bucket:{key}", {"tokens": tokens, "last": now})
            return False
        self.set_json(f"bucket:{key}", {"tokens": tokens - 1, "last": now})
        return True

    def store_confirmation(self, confirmation: Confirmation, ttl_seconds: int) -> Confirmation:
        self.set_json(f"confirmation:{confirmation.token}", asdict(confirmation), ttl_seconds=max(1, ttl_seconds))
        return confirmation

    def get_confirmation(self, token: str) -> Confirmation | None:
        value = self.get_json(f"confirmation:{token}")
        if not value:
            return None
        confirmation = Confirmation(**value)
        if confirmation.expires_at < time.time():
            self.delete(f"confirmation:{token}")
            return None
        return confirmation

    def consume_confirmation(self, token: str) -> Confirmation | None:
        confirmation = self.get_confirmation(token)
        if confirmation is None:
            return None
        self.delete(f"confirmation:{token}")
        return confirmation
