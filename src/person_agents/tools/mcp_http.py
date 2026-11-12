from __future__ import annotations

from typing import Any

import httpx
import structlog

from person_agents.tools.registry import ToolDef, ToolRegistry

logger = structlog.get_logger()


class MCPHTTPClient:
    def __init__(self, server_url: str, *, timeout_s: float = 10.0, token: str | None = None) -> None:
        self.server_url = server_url.rstrip("/")
        self.timeout_s = timeout_s
        self.token = token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, trust_env=False) as client:
                response = await client.request(
                    method,
                    f"{self.server_url}{path}",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("mcp_http_request_failed", server=self.server_url, path=path, error=str(exc))
            raise RuntimeError(f"MCP request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise TypeError("MCP response must be a JSON object")
        return data

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/health")

    async def list_tools(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/tools")
        tools = data.get("tools", data)
        if not isinstance(tools, list):
            raise TypeError("MCP tools response must be an array")
        return tools

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/tools/{tool_name}/call", payload={"arguments": arguments})


def _normalize_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}, "additionalProperties": True}
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return {"type": "object", "properties": {}, "additionalProperties": True}
    return schema


def register_mcp_tools(
    registry: ToolRegistry,
    client: MCPHTTPClient,
    *,
    required_permission: str = "read",
    requires_confirm: bool = False,
) -> list[str]:
    """Synchronize remote tool definitions into the governed local registry."""
    try:
        remote_tools = client.list_tools()
    except RuntimeError as exc:
        logger.warning("mcp_tools_sync_failed", server=client.server_url, error=str(exc))
        return []

    registered: list[str] = []
    for item in remote_tools:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = f"mcp.{item['name']}"
        schema = _normalize_schema(item.get("inputSchema") or item.get("parameters"))
        remote_client = client
        remote_tool_name = str(item["name"])

        async def handler(arguments: dict[str, Any], _client=remote_client, _tool=remote_tool_name) -> dict[str, Any]:
            return await _client.call(_tool, arguments)

        registry.register(ToolDef(
            name=name,
            description=str(item.get("description") or f"Remote MCP tool {remote_tool_name}"),
            parameters=schema,
            handler=handler,
            requires_confirm=requires_confirm,
        ))
        registered.append(name)
    return registered
