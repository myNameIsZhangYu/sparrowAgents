from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GraphServices:
    store: Any
    vector: Any
    registry: Any

    @classmethod
    def default(cls) -> "GraphServices":
        from person_agents.graph.nodes import _get_registry, _get_store, _get_vector

        return cls(store=_get_store(), vector=_get_vector(), registry=_get_registry())
