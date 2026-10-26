from person_agents.graph.nodes import build_planner_context
from person_agents.memory.sqlite_store import SQLiteStore


def test_session_summary_roundtrip_is_isolated(tmp_path):
    store = SQLiteStore(db_path=tmp_path / "memory.db")
    store.upsert_session_summary("s1", "summary", owner_user_id="u1", tenant_id="t1")

    assert store.get_session_summary("s1", owner_user_id="u1", tenant_id="t1") == "summary"
    assert store.get_session_summary("s1", owner_user_id="u2", tenant_id="t1") is None


def test_build_planner_context_bounds_items_and_characters():
    context = build_planner_context(
        [{"role": "user", "content": "x" * 200}],
        [{"output": {"status": "ok", "path": "a" * 200, "operation": "write"}}],
        "s" * 200,
    )

    assert len(context) <= 600


async def test_run_graph_injects_and_updates_session_summary(tmp_path, monkeypatch):
    from person_agents.graph.engine import run_graph
    from person_agents.graph.runtime import GraphServices
    from person_agents.security.context import RequestContext

    store = SQLiteStore(db_path=tmp_path / "memory.db")
    store.upsert_session_summary("s1", "existing", owner_user_id="user", tenant_id="tenant")

    class FakeCompiledGraph:
        async def ainvoke(self, state, config):
            assert state["session_summary"] == "existing"
            return {
                **state,
                "status": "success",
                "tool_results": [{"output": {"status": "ok", "path": "first.txt", "operation": "write"}}],
            }

    def fake_build_graph(*args, **kwargs):
        return FakeCompiledGraph()

    async def fake_available():
        return True

    monkeypatch.setattr("person_agents.graph.engine.build_graph", fake_build_graph)
    monkeypatch.setattr("person_agents.graph.engine.llm_is_available", fake_available)
    services = GraphServices(store=store, vector=object(), registry=object())

    result = await run_graph(
        "创建 first.txt",
        session_id="s1",
        context=RequestContext(user_id="user", tenant_id="tenant", session_id="s1"),
        services=services,
    )

    assert result["status"] == "success"
    assert "first.txt" in store.get_session_summary("s1", owner_user_id="user", tenant_id="tenant")
