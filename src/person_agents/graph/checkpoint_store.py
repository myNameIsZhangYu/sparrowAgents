from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class TaskCheckpoint:
    checkpoint_id: str
    run_id: str
    task_id: str
    status: str
    state: dict[str, Any]


class CheckpointStore(Protocol):
    def upsert_task_checkpoint(
        self,
        checkpoint_id: str,
        run_id: str,
        owner_user_id: str,
        tenant_id: str,
        task_id: str,
        status: str,
        state: dict[str, Any],
    ) -> None: ...

    def get_task_checkpoint(
        self,
        run_id: str,
        owner_user_id: str,
        tenant_id: str,
        task_id: str,
    ) -> dict[str, Any] | None: ...

    def record_task_artifact(
        self,
        artifact_id: str,
        run_id: str,
        owner_user_id: str,
        tenant_id: str,
        task_id: str,
        artifact_type: str,
        uri: str,
        metadata: dict[str, Any],
    ) -> None: ...

    def list_task_artifacts(
        self,
        run_id: str,
        owner_user_id: str,
        tenant_id: str,
    ) -> list[dict[str, Any]]: ...
