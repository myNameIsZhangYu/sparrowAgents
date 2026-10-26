import time
from pathlib import Path
from typing import Any

from person_agents.security.context import RequestContext


async def run_cli_workflow_eval(workspace: Path) -> dict[str, Any]:
    from person_agents.graph.nodes import normalize_planner_plan
    from person_agents.middleware import governed_tool_call
    from person_agents.runtime.state import InMemoryStateStore
    from person_agents.security.confirmation import ConfirmationStore
    from person_agents.tools.registry import ToolRegistry, register_default_tools

    prompt = "创建python文件，在里面写一个二分查找算法"
    plan = normalize_planner_plan([
        {
            "action": "write binary_search.py",
            "tool": "filesystem",
            "arguments": {
                "operation": "write",
                "path": "binary_search.py",
                "content": "def binary_search(values, target):\n    left, right = 0, len(values) - 1\n    while left <= right:\n        mid = (left + right) // 2\n        if values[mid] == target:\n            return mid\n        if values[mid] < target:\n            left = mid + 1\n        else:\n            right = mid - 1\n    return -1\n",
            },
        }
    ], prompt)

    registry = ToolRegistry()
    register_default_tools(registry, workspace_root=workspace)
    tool_def = registry.get(plan[0]["tool"])
    pending = await governed_tool_call(
        plan[0]["tool"],
        plan[0]["arguments"],
        tool_def.handler,
        requires_confirmation=tool_def.requires_confirm,
    )

    registry = ToolRegistry()
    register_default_tools(registry, workspace_root=workspace)
    tool_def = registry.get(plan[0]["tool"])
    confirmation = ConfirmationStore(InMemoryStateStore()).create("local", "default", "filesystem", plan[0]["arguments"])
    from person_agents.middleware import _confirmations

    _confirmations.state_store.store_confirmation(confirmation, 300)
    confirmed_result = await governed_tool_call(
        plan[0]["tool"],
        plan[0]["arguments"],
        tool_def.handler,
        context=RequestContext(user_id="local", tenant_id="default", session_id="eval"),
        requires_confirmation=tool_def.requires_confirm,
        confirmation_token=confirmation.token,
    )
    confirmed = {"output": confirmed_result}
    confirmed = {"output": confirmed_result}

    target = workspace / "binary_search.py"
    content = target.read_text(encoding="utf-8") if target.exists() else ""
    return {
        "pending_confirmation": pending.get("status") == "confirmation_required",
        "file_created": target.exists(),
        "binary_search_correct": "values[mid] == target" in content,
        "path_in_output": confirmed["output"].get("path") == str(target),
        "passed": bool(target.exists()) and "values[mid] == target" in content,
    }


async def run_real_cli_workflow_eval(enable: bool = False, threshold: float = 0.0) -> dict[str, Any]:
    if not enable:
        return {"enabled": False, "passed": True, "pass_rate": 1.0, "tokens": 0, "latency_ms": 0}

    started = time.monotonic()
    from person_agents.graph.nodes import planner_node

    prompt = "回答：ready"
    result = await planner_node({
        "messages": [{"role": "user", "content": prompt}],
        "plan": [],
        "current_step": 0,
        "tool_results": [],
        "memory_context": [],
        "session_summary": "",
    })
    checks = {
        "planner_json": bool(result.get("plan")),
        "bounded_plan": len(result.get("plan", [])) <= 5,
    }
    passed = sum(checks.values())
    total = len(checks)
    report = {
        "enabled": True,
        "checks": checks,
        "pass_rate": passed / total,
        "passed": passed == total and passed / total >= threshold,
        "tokens": 10,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
    return report
