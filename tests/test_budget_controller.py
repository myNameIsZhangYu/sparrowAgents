from person_agents.graph.budget import BudgetController


def test_budget_controller_tracks_usage_and_allows_within_limits():
    controller = BudgetController(max_tokens=100, max_cost_usd=1.0, max_seconds=10)

    controller.consume(tokens=50, cost_usd=0.2)

    assert controller.usage["tokens"] == 50
    assert controller.usage["cost_usd"] == 0.2
    assert controller.usage["seconds"] >= 0
    assert controller.exhausted() is False


def test_budget_controller_blocks_each_exceeded_budget():
    controller = BudgetController(max_tokens=10, max_cost_usd=0.1, max_seconds=0)
    controller.consume(tokens=20, cost_usd=0.2)

    reasons = controller.exhausted_reasons()

    assert set(reasons) == {"tokens", "cost_usd", "seconds"}
    assert controller.exhausted() is True
    assert controller.critic_feedback == "fail: budget exhausted (tokens, cost_usd, seconds)"
