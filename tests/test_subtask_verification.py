from person_agents.graph.verification import verify_subtask_evidence


def test_verify_subtask_requires_explicit_evidence():
    assert verify_subtask_evidence({"status": "completed", "output": "done"}) is False
    assert verify_subtask_evidence({
        "status": "completed",
        "output": "done",
        "verification": {"assertion": "exists", "passed": True},
    }) is True
