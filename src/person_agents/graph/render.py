from pathlib import Path
from typing import Any


def _humanize_operation(operation: str) -> str:
    return {
        "write": "写入",
        "read": "读取",
        "list": "列出",
        "delete": "删除",
    }.get(operation, operation)


def render_confirmation(pending: dict[str, Any], token: str) -> str:
    arguments = pending.get("arguments", {}) if isinstance(pending.get("arguments", {}), dict) else {}
    operation = arguments.get("operation", "")
    path = arguments.get("path", "")
    absolute_path = str(Path(path).resolve()) if path else ""
    action = f"{pending.get('tool', 'tool')} {operation}".strip()
    return f"待确认操作：{action}\n目标路径：{absolute_path}\n请执行：/confirm {token}"


def render_agent_result(result: dict[str, Any]) -> str:
    if result.get("status") == "confirmation_required":
        last_output = result.get("tool_results", [{}])[-1].get("output", {})
        pending = last_output if isinstance(last_output, dict) else {}
        return render_confirmation(pending, str(result.get("pending_confirmation", "")))

    last_output = result.get("tool_results", [{}])[-1].get("output", "") if result.get("tool_results") else ""
    if result.get("status") == "failed":
        error = result.get("error") or last_output
        if isinstance(error, dict):
            error = error.get("message") or error.get("error") or "未知错误"
        return f"任务失败：{error}\n请重试或换一种描述。"

    if isinstance(last_output, dict):
        path = last_output.get("path", "")
        operation = last_output.get("operation", "")
        if path:
            absolute_path = str(Path(path).resolve())
            operation_text = _humanize_operation(str(operation))
            return f"已执行 {last_output.get('tool', 'filesystem')} {operation_text}：{absolute_path}"
        if last_output.get("status") == "ok":
            return "任务完成。"
        return str(last_output)

    output = str(last_output)
    if output.lstrip().startswith(("[", "{")) and ('"action"' in output or '"tool"' in output):
        return "任务已完成。执行摘要：请补充或确认下一步。"
    return output
