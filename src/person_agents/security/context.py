from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    # 每个请求的安全上下文；所有存储和工具调用都必须从这里传递。
    user_id: str
    tenant_id: str
    session_id: str
    role: str = "user"

