import uuid

from person_agents.config.settings import settings
from person_agents.memory.sqlite_store import SQLiteStore


class JobManager:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        if not isinstance(store, SQLiteStore):
            self.registry = None
        else:
            from person_agents.runtime.config_registry import ConfigRegistry

            self.registry = ConfigRegistry(store)

    def create(self, prompt: str, owner_user_id: str, tenant_id: str, session_id: str, provenance: dict[str, str] | None = None) -> str:
        run_id = str(uuid.uuid4())
        self.store.create_run(run_id, prompt, owner_user_id, tenant_id, session_id, provenance=provenance)
        return run_id

    def get(self, run_id: str, owner_user_id: str, tenant_id: str) -> dict | None:
        return self.store.get_run(run_id, owner_user_id, tenant_id)

    def update(self, run_id: str, owner_user_id: str, tenant_id: str, status: str, output: str | None = None, confirmation_token: str | None = None, error: str | None = None) -> bool:
        return self.store.update_run(run_id, owner_user_id, tenant_id, status, output, confirmation_token, error)

    def next(self) -> dict | None:
        return self.store.next_queued_run()

    def claim_next(self, worker_id: str, lease_seconds: int = 60) -> dict | None:
        return self.store.claim_next_run(worker_id, lease_seconds)

    def renew_lease(self, run_id: str, worker_id: str, lease_seconds: int = 60) -> bool:
        return self.store.renew_run_lease(run_id, worker_id, lease_seconds)

    async def default_provenance(self) -> dict[str, str]:
        if self.registry is None:
            versions = {
                "prompt": settings.prompt_version,
                "tool": settings.tool_config_version,
                "policy": settings.policy_version,
            }
        else:
            versions = self.registry.register_defaults()
        return {
            "agent_version": settings.agent_version,
            "prompt_version": versions["prompt"],
            "tool_config_version": versions["tool"],
            "policy_version": versions["policy"],
        }
