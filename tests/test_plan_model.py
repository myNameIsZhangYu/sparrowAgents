import pytest

from person_agents.graph.plan_model import PlanModel


def test_plan_model_parses_legacy_list_and_assigns_ids():
    plan = PlanModel.parse([
        {"action": "collect", "tool": "filesystem", "arguments": {"operation": "read", "path": "a"}},
        {"action": "summarize", "tool": "none"},
    ], objective="demo")

    assert plan.objective == "demo"
    assert [step.id for step in plan.steps] == ["task-0", "task-1"]
    assert plan.steps[1].depends_on == ["task-0"]
    assert plan.to_legacy() == [
        {"id": "task-0", "action": "collect", "tool": "filesystem", "arguments": {"operation": "read", "path": "a"}, "depends_on": [], "milestone_id": None, "risk": "medium", "timeout_s": None, "agent": None, "status": "pending"},
        {"id": "task-1", "action": "summarize", "tool": "none", "arguments": {}, "depends_on": ["task-0"], "milestone_id": None, "risk": "medium", "timeout_s": None, "agent": None, "status": "pending"},
    ]


def test_plan_model_preserves_milestones_budget_and_risk():
    plan = PlanModel.parse({
        "objective": "build report",
        "budget": {"max_tokens": 1000, "max_seconds": 60},
        "milestones": [{"id": "m1", "name": "data"}],
        "steps": [
            {"action": "read", "tool": "filesystem", "milestone_id": "m1", "risk": "low", "timeout_s": 10},
            {"action": "write", "tool": "filesystem", "milestone_id": "m1", "risk": "medium", "timeout_s": 20},
        ],
    }, objective="fallback objective")

    assert plan.budget == {"max_tokens": 1000, "max_seconds": 60}
    assert plan.milestones == [{"id": "m1", "name": "data"}]
    assert {step.risk for step in plan.steps} == {"low", "medium"}
    assert {step.timeout_s for step in plan.steps} == {10, 20}


def test_plan_model_rejects_cyclic_dependency():
    with pytest.raises(ValueError, match="plan dependency cycle"):
        PlanModel.parse([
            {"action": "a", "depends_on": ["task-1"]},
            {"action": "b", "depends_on": ["task-0"]},
        ], objective="cycle")


def test_plan_model_degrades_missing_or_invalid_dependency_ids():
    plan = PlanModel.parse([
        {"action": "a", "depends_on": ["missing"], "risk": "not-a-level"},
    ], objective="invalid")

    assert plan.steps[0].depends_on == []
    assert plan.steps[0].risk == "medium"
