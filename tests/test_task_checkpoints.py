from person_agents.memory.sqlite_store import SQLiteStore


def test_task_checkpoint_upsert_and_owner_isolation(tmp_path):
    store = SQLiteStore(tmp_path / "memory.db")
    store.upsert_task_checkpoint(
        "cp-1", "run-1", "alice", "tenant", "task-0", "running",
        {"output": "partial", "attempt": 1},
    )
    store.upsert_task_checkpoint(
        "cp-2", "run-1", "alice", "tenant", "task-0", "completed",
        {"output": "done", "attempt": 1},
    )

    checkpoint = store.get_task_checkpoint("run-1", "alice", "tenant", "task-0")
    assert checkpoint["checkpoint_id"] == "cp-2"
    assert checkpoint["state"] == {"output": "done", "attempt": 1}
    assert store.get_task_checkpoint("run-1", "bob", "tenant", "task-0") is None


def test_task_artifacts_record_and_list(tmp_path):
    store = SQLiteStore(tmp_path / "memory.db")
    store.record_task_artifact(
        "artifact-1", "run-1", "alice", "tenant", "task-0", "file",
        "/workspace/report.md", {"sha256": "abc"},
    )

    artifacts = store.list_task_artifacts("run-1", "alice", "tenant")
    assert len(artifacts) == 1
    assert artifacts[0]["uri"] == "/workspace/report.md"
    assert artifacts[0]["metadata"] == {"sha256": "abc"}
    assert store.list_task_artifacts("run-1", "bob", "tenant") == []
