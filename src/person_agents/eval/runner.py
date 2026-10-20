from dataclasses import dataclass
from typing import Any

import structlog

from person_agents.eval.agent_cases import AgentEvalCase, production_agent_cases
from person_agents.llm.fallback import LLMFallbackError, call_with_fallback
from person_agents.observability.cost import CostTracker
from person_agents.security.context import RequestContext

logger = structlog.get_logger()


@dataclass
class EvalCase:
    # 简单文本评估：只要求响应包含关键片段。
    name: str
    input: str
    expected_contains: str
    category: str


class EvalRunner:
    def __init__(self) -> None:
        # eval 独立计费，避免污染在线用户用量。
        self.tracker = CostTracker(session_id="eval")

    async def run_case(self, case: EvalCase) -> dict[str, Any]:
        messages = [{"role": "user", "content": case.input}]
        try:
            # 直接调用 fallback 链，用于检查模型可用性与基础输出质量。
            resp = await call_with_fallback(messages)
            self.tracker.record(resp.model, resp.prompt_tokens, resp.completion_tokens, 0)
            passed = case.expected_contains.lower() in resp.content.lower()
            return {"name": case.name, "passed": passed, "output": resp.content[:200], "degraded": resp.degraded}
        except (ConnectionError, TimeoutError, LLMFallbackError) as exc:
            return {"name": case.name, "passed": False, "output": str(exc), "degraded": True}

    async def run_agent_case(self, case: AgentEvalCase) -> dict[str, Any]:
        from person_agents.graph.engine import run_graph

        # eval 使用固定租户/会话，便于识别并隔离测试数据。
        context = RequestContext(user_id="eval", tenant_id="eval", session_id=f"eval-{case.name}")
        try:
            state = await run_graph(case.input, session_id=context.session_id, context=context)
            # 多 agent 评估不仅看结果，还看完整路由历史是否符合拓扑预期。
            passed = tuple(state.get("agent_history", [])) == case.expected_agent_history and state.get("status", "success") == case.expected_status
            return {
                "name": case.name,
                "passed": passed,
                "output": str(state.get("tool_results", []))[:200],
                "agent_history": state.get("agent_history", []),
                "status": state.get("status", "success"),
            }
        except (ConnectionError, TimeoutError, LLMFallbackError) as exc:
            return {"name": case.name, "passed": False, "output": str(exc), "degraded": True}

    async def run_agent_cases(self, cases: list[AgentEvalCase] | None = None) -> dict[str, Any]:
        # 批量运行并输出通过率、失败数和 token 消耗，作为 CI/发布质量门。
        cases = cases or production_agent_cases()
        results = [await self.run_agent_case(case) for case in cases]
        total = len(results)
        passed = sum(result["passed"] for result in results)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / total if total else 0,
            "total_tokens": self.tracker.total_tokens,
            "results": results,
            "model": "multi-agent-runtime",
        }

    async def run_all(self, cases: list[EvalCase]) -> dict[str, Any]:
        # 基础文本评估入口，与多 agent 拓扑评估分开。
        results = [await self.run_case(c) for c in cases]
        total = len(results)
        passed = sum(r["passed"] for r in results)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": passed / total if total else 0,
            "total_tokens": self.tracker.total_tokens,
            "results": results,
            "model": results[0].get("model", "unknown") if results else "unknown",
        }
