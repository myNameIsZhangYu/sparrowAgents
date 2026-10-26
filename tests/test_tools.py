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
async def test_filesystem_practical_operations(tmp_path):
    from person_agents.tools.mcp_filesystem import MCPFilesystemTool

    tool = MCPFilesystemTool(root_dir=tmp_path)
    source = tmp_path / "source.txt"
    assert (await tool.call({"operation": "write", "path": "source.txt", "content": "abc"}))["status"] == "ok"
    assert (await tool.call({"operation": "append", "path": "source.txt", "content": "d"}))["bytes_appended"] == 1
    assert source.read_text() == "abcd"

    copied = await tool.call({"operation": "copy", "path": "source.txt", "destination": "dir/copy.txt"})
    moved = await tool.call({"operation": "move", "path": "dir/copy.txt", "destination": "dir/moved.txt"})
    mkdir = await tool.call({"operation": "mkdir", "path": "nested"})
    search = await tool.call({"operation": "search", "path": ".", "pattern": "moved"})

    assert copied["status"] == moved["status"] == mkdir["status"] == search["status"] == "ok"
    assert (tmp_path / "dir" / "moved.txt").exists()
    assert (tmp_path / "nested").is_dir()
    assert search["items"][0]["path"] == "dir/moved.txt"


def test_filesystem_tool_description_covers_all_operations():
    from person_agents.tools.registry import ToolRegistry, register_default_tools

    registry = ToolRegistry()
    register_default_tools(registry, workspace_root="/tmp/tools")
    definition = registry.get("filesystem")

    for operation in ("read", "write", "list", "delete", "append", "copy", "move", "mkdir", "search"):
        assert operation in definition.description


async def test_filesystem_rejects_destination_escape(tmp_path):
    from person_agents.tools.mcp_filesystem import MCPFilesystemTool

    tool = MCPFilesystemTool(root_dir=tmp_path)
    result = await tool.call({"operation": "copy", "path": "a.txt", "destination": "../outside.txt"})

    assert result["status"] == "error"
    assert "escapes sandbox root" in result["message"]
