from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    user_id: str
    tenant_id: str
    session_id: str
    role: str = "user"

