import httpx
import pytest

from person_agents.tools.mcp_http import MCPHTTPClient, register_mcp_tools
from person_agents.tools.registry import ToolRegistry


def test_register_mcp_tools_normalizes_contract_and_registers_handler():
    class Client:
        server_url = "http://mcp.local"

        def list_tools(self):
            return [{
                "name": "search",
                "description": "Search documents",
                "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
            }]

    registry = ToolRegistry()
    registered = register_mcp_tools(registry, Client())

    assert registered == ["mcp.search"]
    tool = registry.get("mcp.search")
    assert tool.parameters == {"type": "object", "properties": {"query": {"type": "string"}}}
    assert tool.requires_confirm is False


def test_register_mcp_tools_degrades_on_invalid_contract():
    class Invalid:
        server_url = "http://mcp.local"

        def list_tools(self):
            return [{"description": "missing name"}, "bad", {"name": "valid"}]

    registry = ToolRegistry()
    registered = register_mcp_tools(registry, Invalid())

    assert registered == ["mcp.valid"]
    assert registry.get("mcp.valid").parameters["type"] == "object"


async def test_mcp_client_uses_no_system_proxy_and_posts_arguments():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path, request.headers.get("Authorization")))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"status": "ok", "arguments": {"query": "hello"}})

    transport = httpx.MockTransport(handler)
    client = MCPHTTPClient("http://mcp.local", token="secret")
    original_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    import person_agents.tools.mcp_http as module
    module.httpx.AsyncClient = client_factory
    try:
        assert await client.health() == {"status": "ok"}
        result = await client.call("search", {"query": "hello"})
        assert result["arguments"] == {"query": "hello"}
    finally:
        module.httpx.AsyncClient = original_async_client

    assert requests == [
        ("GET", "/health", "Bearer secret"),
        ("POST", "/tools/search/call", "Bearer secret"),
    ]


async def test_mcp_client_rejects_non_object_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    client = MCPHTTPClient("http://mcp.local")
    original_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    import person_agents.tools.mcp_http as module
    module.httpx.AsyncClient = client_factory
    try:
        with pytest.raises(TypeError, match="MCP response must be a JSON object"):
            await client.health()
    finally:
        module.httpx.AsyncClient = original_async_client
