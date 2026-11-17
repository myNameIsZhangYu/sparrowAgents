from person_agents.runtime.worker import restore_completed_subtasks


def test_restore_completed_subtasks_requires_verification_evidence():
    class Store:
        def list_task_checkpoints(self, run_id, owner_user_id, tenant_id):
            return [
                {"task_id": "task-0", "status": "completed", "state": {"output": "done", "verification": {"passed": True}}},
                {"task_id": "task-1", "status": "completed", "state": {"output": "unverified"}},
            ]

    subtasks = [
        {"id": "task-0", "status": "pending"},
        {"id": "task-1", "status": "pending"},
    ]
    restored = restore_completed_subtasks(subtasks, Store(), "run", "alice", "tenant")

    assert restored[0]["status"] == "completed"
    assert restored[0]["result"]["verification"]["passed"] is True
    assert restored[1]["status"] == "pending"
