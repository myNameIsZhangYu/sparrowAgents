from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GraphServices:
    # 图运行时依赖容器；注入后避免节点读取模块级全局状态。
    store: Any
    vector: Any
    registry: Any

    @classmethod
    def default(cls) -> "GraphServices":
        from person_agents.graph.nodes import _get_registry, _get_store, _get_vector

        # 默认组合适用于 CLI 与本地服务；测试可替换任意依赖。
        return cls(store=_get_store(), vector=_get_vector(), registry=_get_registry())
