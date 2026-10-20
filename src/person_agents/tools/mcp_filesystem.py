from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


class MCPFilesystemTool:
    """MCP-compatible filesystem tool using direct file I/O.

    Provides read, write, list, and delete operations within a sandboxed root directory.
    """

    def __init__(self, root_dir: str | Path):
        # root 解析后固定为绝对路径，后续所有路径必须落在这个目录内。
        self.root = Path(root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        # 解析 .. 和符号链接后校验是否越界，这是文件工具的核心安全边界。
        resolved = (self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise PermissionError(f"Path escapes sandbox root: {path}")
        return resolved

    async def call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        # 工具返回结构化错误，不把文件异常直接抛到图执行层。
        operation = arguments.get("operation", "")
        path = arguments.get("path", "")
        try:
            target = self._resolve(path)
            if operation == "read":
                return {"status": "ok", "content": target.read_text(encoding="utf-8")}
            if operation == "write":
                content = arguments.get("content", "")
                # 写入前自动创建父目录，保持一次性工具调用语义简单。
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                return {"status": "ok", "bytes_written": len(content)}
            if operation == "list":
                if target.is_dir():
                    items = [{"name": p.name, "is_dir": p.is_dir()} for p in sorted(target.iterdir())]
                else:
                    items = [{"name": target.name, "is_dir": False}]
                return {"status": "ok", "items": items}
            if operation == "delete":
                if target.is_file():
                    target.unlink()
                elif target.is_dir():
                    import shutil
                    shutil.rmtree(target)
                return {"status": "ok"}
            return {"status": "error", "message": f"Unknown operation: {operation}"}
        except PermissionError as exc:
            return {"status": "error", "message": str(exc)}
        except FileNotFoundError:
            return {"status": "error", "message": f"Path not found: {path}"}
        except OSError as exc:
            return {"status": "error", "message": str(exc)}
