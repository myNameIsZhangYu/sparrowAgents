from person_agents.config.settings import settings
from person_agents.graph.runtime import GraphServices


def test_default_services_sync_mcp_tools(tmp_path, monkeypatch):
    class Client:
        server_url = "http://mcp.local"

        def list_tools(self):
            return [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr(settings, "mcp_server_url", "http://mcp.local")
    monkeypatch.setattr(
        "person_agents.tools.mcp_http.MCPHTTPClient",
        lambda *args, **kwargs: Client(),
    )

    services = GraphServices.default(workspace_root=tmp_path)
    assert services.registry.get("mcp.echo") is not None
