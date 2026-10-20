import secrets
import time
from dataclasses import dataclass
from typing import Any

from person_agents.runtime.state import StateStore


@dataclass
class Confirmation:
    # 一次性确认绑定用户、租户、工具和参数，防止令牌跨上下文重放。
    token: str
    owner_user_id: str
    tenant_id: str
    tool_name: str
    arguments: dict[str, Any]
    expires_at: float


class ConfirmationStore:
    def __init__(self, state_store: StateStore, ttl_seconds: int = 300) -> None:
        self.state_store = state_store
        self.ttl_seconds = ttl_seconds

    def create(self, owner_user_id: str, tenant_id: str, tool_name: str, arguments: dict[str, Any]) -> Confirmation:
        # 32-byte URL-safe token，配合 TTL 限制窗口期。
        token = secrets.token_urlsafe(32)
        confirmation = Confirmation(
            token=token,
            owner_user_id=owner_user_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            arguments=arguments,
            expires_at=time.time() + self.ttl_seconds,
        )
        return self.state_store.store_confirmation(confirmation, self.ttl_seconds)

    def get(self, token: str) -> Confirmation | None:
        return self.state_store.get_confirmation(token)

    def consume(self, token: str) -> Confirmation | None:
        # 消费即删除；拒绝和确认都可能调用此方法。
        return self.state_store.consume_confirmation(token)
