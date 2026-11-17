from person_agents.graph.replanner import choose_repair_action


def test_replanner_maps_failure_types():
    cases = {
        "parameters": {"action": "repair_parameters"},
        "tool": {"action": "replace_tool"},
        "permission": {"action": "abandon"},
        "timeout": {"action": "increase_timeout"},
        "model_output": {"action": "regenerate"},
        "dependency_failure": {"action": "retry_dependency"},
    }
    for failure_type, expected in cases.items():
        decision = choose_repair_action(failure_type, attempt=1)
        assert decision["action"] == expected["action"]


def test_replanner_caps_repeated_repairs():
    decision = choose_repair_action("tool", attempt=3)

    assert decision["action"] == "abandon"
    assert decision["reason"] == "repair attempt limit reached"
