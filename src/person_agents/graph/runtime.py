from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GraphServices:
    # 图运行时依赖容器；注入后避免节点读取模块级全局状态。
    store: Any
    vector: Any
    registry: Any

    @property
    def confirmation_store(self) -> Any:
        from person_agents.middleware import get_confirmation_store

        return get_confirmation_store()

    @classmethod
    def default(cls, workspace_root: str | Path | None = None) -> "GraphServices":
        from person_agents.config.settings import settings
        from person_agents.graph.nodes import _get_registry, _get_store, _get_vector
        from person_agents.tools.mcp_http import MCPHTTPClient, register_mcp_tools

        # 默认组合适用于本地服务；CLI 可以注入自己的工作区，测试可替换任意依赖。
        if workspace_root is None:
            registry = _get_registry()
        else:
            from person_agents.tools.registry import ToolRegistry, register_default_tools

            registry = ToolRegistry()
            register_default_tools(registry, workspace_root=str(workspace_root))
        if settings.mcp_server_url:
            register_mcp_tools(
                registry,
                MCPHTTPClient(
                    settings.mcp_server_url,
                    timeout_s=settings.mcp_timeout_s,
                    token=settings.mcp_server_token or None,
                ),
            )
        return cls(store=_get_store(), vector=_get_vector(), registry=registry)
