import pytest

from person_agents.eval.super_agent_metrics import evaluate_super_agent_report


def test_super_agent_metrics_aggregates_all_gates():
    report = {
        "threshold": 0.8,
        "results": [
            {"passed": True, "dag_completed": True, "retry_count": 1, "tokens": 10, "cost_usd": 0.01, "latency_ms": 100},
            {"passed": True, "dag_completed": True, "recovered": True, "recoverable": True, "tokens": 20, "cost_usd": 0.02, "latency_ms": 300},
            {"passed": False, "dag_completed": False, "security_events": ["policy_denied"], "tokens": 5, "cost_usd": 0.005, "latency_ms": 600},
        ],
    }

    metrics = evaluate_super_agent_report(report)

    assert metrics["total"] == 3
    assert metrics["passed"] == 2
    assert metrics["pass_rate"] == pytest.approx(2 / 3)
    assert metrics["retry_rate"] == pytest.approx(1 / 3)
    assert metrics["recovery_rate"] == 1.0
    assert metrics["security_events"] == 1
    assert metrics["total_tokens"] == 35
    assert metrics["total_cost_usd"] == pytest.approx(0.035)
    assert metrics["dag_completed"] is False
    assert metrics["quality_gate_passed"] is False


def test_super_agent_metrics_aggregates_runtime_quality():
    report = {
        "threshold": 0.8,
        "results": [
            {"passed": True, "budget_adhered": True, "repair_success": True, "verified_subtasks": 3, "subtasks": 3},
            {"passed": True, "budget_adhered": False, "repair_success": False, "verified_subtasks": 1, "subtasks": 2},
        ],
    }

    metrics = evaluate_super_agent_report(report)

    assert metrics["budget_adherence"] == 0.5
    assert metrics["repair_success_rate"] == 0.5
    assert metrics["verification_evidence_rate"] == 0.8


def test_super_agent_metrics_passes_clean_report():
    report = {
        "threshold": 0.8,
        "results": [
            {"passed": True, "dag_completed": True, "tokens": 10, "cost_usd": 0.01, "latency_ms": 50},
        ],
    }

    metrics = evaluate_super_agent_report(report)

    assert metrics["score"] == 1.0
    assert metrics["recovery_rate"] == 1.0
    assert metrics["quality_gate_passed"] is True
