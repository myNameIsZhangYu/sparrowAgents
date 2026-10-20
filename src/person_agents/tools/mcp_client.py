import structlog

logger = structlog.get_logger()


class MCPTool:
    """Wrapper for MCP protocol tool calls.

    In a full implementation this would use the MCP SDK to connect to an MCP server.
    For now this provides the interface that LangGraph nodes can call.
    """

    def __init__(self, server_url: str, tool_name: str):
        # 记录 MCP 服务地址与工具名，供后续 SDK 客户端建立连接时使用。
        self.server_url = server_url
        self.tool_name = tool_name

    async def call(self, arguments: dict) -> dict:
        # 输出调用上下文，便于在 MCP 真正接入前追踪调用链路。
        logger.info("mcp_call", server=self.server_url, tool=self.tool_name)
        # Placeholder: replace with MCP SDK client when available
        return {"status": "not_implemented", "tool": self.tool_name, "arguments": arguments}
