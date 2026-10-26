from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class S(TypedDict):
    value: str


async def node_a(state: S):
    return {"value": state["value"] + "-a"}


async def route(state: S) -> str:
    print("ROUTER", state, flush=True)
    return END


def test_probe():
    import asyncio

    builder = StateGraph(S)
    builder.add_node("a", node_a)
    builder.add_edge(START, "a")
    builder.add_conditional_edges("a", route, {END: END})
    graph = builder.compile()

    async def run():
        result = await asyncio.wait_for(graph.ainvoke({"value": "x"}, {"configurable": {"thread_id": "probe"}}), 3)
        return result

    result = asyncio.run(run())
    print("AFTER", flush=True)
    print(result, flush=True)
    assert result["value"] == "x-a"
