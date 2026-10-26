from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from person_agents.tools.mcp_filesystem import MCPFilesystemTool
from person_agents.tools.sandbox import DockerSandbox

logger = structlog.get_logger()


@dataclass
class ToolDef:
    # 工具定义是 planner 能看到的稳定契约：schema 决定参数，handler 是唯一执行入口。
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any]]
    requires_confirm: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        # 以工具名做唯一键，防止重复注册导致模型选择歧义。
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        self._tools[tool.name] = tool
        logger.info("tool_registered", tool=tool.name)

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDef]:
        return list(self._tools.values())

    def to_openai_schema(self) -> list[dict[str, Any]]:
        # 转换为 OpenAI function calling 使用的 JSON Schema。
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]


def register_default_tools(registry: ToolRegistry, workspace_root: str = "/tmp/person-agents-workspace") -> None:
    # 两个默认工具都属于高风险操作，因此要求一次性人工确认。
    fs = MCPFilesystemTool(root_dir=workspace_root)
    sandbox = DockerSandbox()

    async def fs_handler(arguments: dict) -> dict:
        # 文件系统工具内部负责 root 路径校验。
        return await fs.call(arguments)

    registry.register(ToolDef(
        name="filesystem",
        description="read, write, list, delete, append, copy, move, mkdir, or search files in a sandboxed workspace directory",
        parameters={
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["read", "write", "list", "delete", "append", "copy", "move", "mkdir", "search"]},
                "path": {"type": "string"},
                "content": {"type": "string"},
                "destination": {"type": "string"},
                "pattern": {"type": "string"},
            },
            "required": ["operation", "path"],
        },
        handler=fs_handler,
        requires_confirm=True,
    ))

    async def shell_handler(arguments: dict) -> dict:
        command = arguments.get("command", "")
        # shell 命令必须在无网络、有资源上限的 Docker 容器中执行。
        result = await sandbox.execute(command)
        # 限制输出长度，避免日志/上下文被大输出撑爆。
        return {"exit_code": result.exit_code, "stdout": result.stdout[:500], "stderr": result.stderr[:500]}

    registry.register(ToolDef(
        name="shell",
        description="Execute a shell command inside a Docker sandbox with resource limits and no network",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
        handler=shell_handler,
        requires_confirm=True,
    ))
