from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RunStore(Protocol):
    # 运行任务存储最小契约，便于替换 SQLite/PostgreSQL 实现。
    def create_run(
        self,
        run_id: str,
        prompt: str,
        owner_user_id: str,
        tenant_id: str,
        session_id: str,
        status: str = "queued",
        provenance: dict[str, str] | None = None,
    ) -> None: ...

    def update_run(
        self,
        run_id: str,
        owner_user_id: str,
        tenant_id: str,
        status: str,
        output: str | None = None,
        confirmation_token: str | None = None,
        error: str | None = None,
        total_tokens: int = 0,
        total_cost: float = 0.0,
    ) -> bool: ...

    def get_run(self, run_id: str, owner_user_id: str, tenant_id: str) -> dict[str, Any] | None: ...
    def queue_depth(self) -> int: ...
    def running_depth(self) -> int: ...
    def dead_letter_depth(self) -> int: ...


@runtime_checkable
class MemoryStore(Protocol):
    # 结构化记忆契约；语义检索由向量库另行实现。
    def upsert(self, key: str, value: str, category: str = "fact", owner_user_id: str = "local", tenant_id: str = "default") -> None: ...
    def get(self, key: str, owner_user_id: str = "local", tenant_id: str = "default") -> dict[str, Any] | None: ...
    def search(self, query: str, category: str | None = None, limit: int = 10, owner_user_id: str = "local", tenant_id: str = "default") -> list[dict[str, Any]]: ...
    def delete(self, key: str, owner_user_id: str = "local", tenant_id: str = "default") -> bool: ...
