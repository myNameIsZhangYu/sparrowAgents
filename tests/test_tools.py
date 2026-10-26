from pathlib import Path

import pytest

from person_agents.tools.mcp_filesystem import MCPFilesystemTool
from person_agents.tools.registry import ToolRegistry, register_default_tools


@pytest.fixture
def fs(tmp_path: Path) -> MCPFilesystemTool:
    return MCPFilesystemTool(root_dir=tmp_path / "workspace")


class TestMCPFilesystem:
    async def test_write_and_read(self, fs: MCPFilesystemTool) -> None:
        result = await fs.call({"operation": "write", "path": "test.txt", "content": "hello"})
        assert result["status"] == "ok"
        assert result["path"] == str(fs.root / "test.txt")
        assert result["bytes_written"] == 5
        result = await fs.call({"operation": "read", "path": "test.txt"})
        assert result["content"] == "hello"

    async def test_list(self, fs: MCPFilesystemTool) -> None:
        await fs.call({"operation": "write", "path": "a.txt", "content": "a"})
        await fs.call({"operation": "write", "path": "b.txt", "content": "b"})
        result = await fs.call({"operation": "list", "path": "."})
        names = [i["name"] for i in result["items"]]
        assert "a.txt" in names
        assert "b.txt" in names

    async def test_delete(self, fs: MCPFilesystemTool) -> None:
        await fs.call({"operation": "write", "path": "del.txt", "content": "x"})
        result = await fs.call({"operation": "delete", "path": "del.txt"})
        assert result["status"] == "ok"

    async def test_path_escape(self, fs: MCPFilesystemTool) -> None:
        result = await fs.call({"operation": "read", "path": "../../etc/passwd"})
        assert result["status"] == "error"


class TestToolRegistry:
    def test_register_default_tools(self) -> None:
        registry = ToolRegistry()
        register_default_tools(registry)
        tool = registry.get("filesystem")
        assert tool is not None
        assert "filesystem" in [t.name for t in registry.list_tools()]

    def test_openai_schema(self) -> None:
        registry = ToolRegistry()
        register_default_tools(registry)
        schema = registry.to_openai_schema()
        assert len(schema) > 0
        assert schema[0]["type"] == "function"
