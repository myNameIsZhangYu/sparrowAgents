from __future__ import annotations

from typing import Any


def evaluate_super_agent_report(report: dict[str, Any]) -> dict[str, Any]:
    results = report.get("results", [])
    total = len(results)
    passed = sum(bool(result.get("passed")) for result in results)
    retried = sum(int(result.get("retry_count", 0)) for result in results)
    security_events = sum(len(result.get("security_events", [])) for result in results)
    recovered = sum(bool(result.get("recovered")) for result in results)
    failed_recoverable = sum(bool(result.get("recoverable")) for result in results)

    pass_rate = passed / total if total else 0.0
    retry_rate = retried / total if total else 0.0
    recovery_rate = recovered / failed_recoverable if failed_recoverable else 1.0
    security_penalty = min(security_events * 0.1, 0.5)
    avg_latency_ms = sum(float(r.get("latency_ms", 0)) for r in results) / max(total, 1)
    latency_penalty = min(max((avg_latency_ms - 5_000) / 10_000, 0), 0.2)
    score = max(0.0, min(1.0, pass_rate - security_penalty - latency_penalty))

    return {
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "retry_rate": retry_rate,
        "recovery_rate": recovery_rate,
        "security_events": security_events,
        "avg_latency_ms": avg_latency_ms,
        "total_tokens": sum(int(result.get("tokens", 0)) for result in results),
        "total_cost_usd": sum(float(result.get("cost_usd", 0)) for result in results),
        "dag_completed": all(bool(result.get("dag_completed")) for result in results),
        "score": score,
        "quality_gate_passed": score >= float(report.get("threshold", 0.8)) and security_events == 0,
    }
