from pathlib import Path

from person_agents.graph.render import render_agent_result, render_confirmation


def test_render_agent_result_prefers_last_tool_output():
    result = {
        "status": "completed",
        "tool_results": [
            {"output": "old"},
            {"tool": "filesystem", "output": {"status": "ok", "path": "/tmp/a.py", "operation": "write"}},
        ],
    }

    assert render_agent_result(result) == "已执行 filesystem 写入：/tmp/a.py"


def test_render_agent_result_keeps_plain_text():
    result = {"tool_results": [{"output": "你好"}]}

    assert render_agent_result(result) == "你好"


def test_render_agent_result_renders_failure_without_raw_json():
    result = {
        "status": "failed",
        "error": None,
        "tool_results": [{"output": {"status": "error", "message": "raw failure"}}],
    }

    text = render_agent_result(result)
    assert "任务失败：raw failure" in text
    assert "请重试或换一种描述" in text


def test_render_confirmation_includes_action_path_and_token():
    text = render_confirmation(
        {"tool": "filesystem", "arguments": {"operation": "write", "path": "a.py"}},
        "token-1",
    )

    assert "待确认操作：filesystem write" in text
    assert "目标路径：" in text
    assert "/confirm token-1" in text


def test_render_agent_result_does_not_expose_planner_json():
    result = {
        "tool_results": [{"output": '[{"action":"write file","tool":"filesystem"}]'}],
    }

    text = render_agent_result(result)

    assert '"tool":"filesystem"' not in text
    assert text.startswith("任务已完成。执行摘要：")


def test_render_agent_result_renders_confirmation(tmp_path: Path):
    result = {
        "status": "confirmation_required",
        "pending_confirmation": "token-1",
        "tool_results": [
            {"output": {"status": "confirmation_required", "tool": "filesystem", "arguments": {"operation": "write", "path": "a.py"}}}
        ],
    }

    assert "/confirm token-1" in render_agent_result(result)
