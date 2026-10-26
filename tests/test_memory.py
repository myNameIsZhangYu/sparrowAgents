from pathlib import Path

import pytest

from person_agents.config.settings import settings
from person_agents.memory.sqlite_store import SQLiteStore
from person_agents.memory.vector_store import VectorStore


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(db_path=tmp_path / "test.db")


class TestSQLiteStore:
    def test_user_isolation(self, store: SQLiteStore) -> None:
        store.upsert("k", "alice", owner_user_id="a", tenant_id="t")
        store.upsert("k", "bob", owner_user_id="b", tenant_id="t")
        assert store.get("k", owner_user_id="a", tenant_id="t")["value"] == "alice"
        assert store.search("alice", owner_user_id="b", tenant_id="t") == []
        assert store.delete("k", owner_user_id="a", tenant_id="t") is True
        assert store.delete("k", owner_user_id="a", tenant_id="t") is False

    def test_upsert_and_get(self, store: SQLiteStore) -> None:
        store.upsert("pref_style", "concise replies", category="preference")
        result = store.get("pref_style")
        assert result is not None
        assert result["value"] == "concise replies"

    def test_upsert_updates_existing(self, store: SQLiteStore) -> None:
        store.upsert("pref_style", "verbose")
        store.upsert("pref_style", "concise")
        assert store.get("pref_style")["value"] == "concise"

    def test_search(self, store: SQLiteStore) -> None:
        store.upsert("fact1", "user likes Python")
        store.upsert("fact2", "user dislikes Java")
        results = store.search("Python")
        assert len(results) == 1

    def test_delete(self, store: SQLiteStore) -> None:
        store.upsert("key1", "val1")
        assert store.delete("key1") is True
        assert store.get("key1") is None

    def test_wal_mode(self, store: SQLiteStore) -> None:
        conn = store.connect()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_agent_collaboration_events_are_persisted(self, store: SQLiteStore) -> None:
        event = {
            "session_id": "session-1",
            "event_type": "result",
            "from_agent": "tool",
            "to_agent": "critic",
            "task_id": "task-0",
            "correlation_id": "corr-1",
            "payload": {"status": "completed"},
        }
        store.append_agent_collaboration_event(
            session_id=event["session_id"],
            event_type=event["event_type"],
            from_agent=event["from_agent"],
            to_agent=event["to_agent"],
            task_id=event["task_id"],
            correlation_id=event["correlation_id"],
            payload=event["payload"],
        )
        store.append_agent_collaboration_event(
            session_id=event["session_id"],
            event_type=event["event_type"],
            from_agent=event["from_agent"],
            to_agent=event["to_agent"],
            task_id=event["task_id"],
            correlation_id=event["correlation_id"],
            payload=event["payload"],
        )

        events = store.list_agent_collaboration_events(event["session_id"], event["correlation_id"])

        assert len(events) == 1
        assert events[0]["payload"] == event["payload"]
        assert store.list_agent_collaboration_events(event["session_id"], "missing") == []


class TestVectorStore:
    def test_user_isolated_query(self, tmp_path: Path) -> None:
        alice = VectorStore(persist_dir=tmp_path / "alice", model_cache_path=settings.chroma_model_cache_path)
        bob = VectorStore(persist_dir=tmp_path / "alice", model_cache_path=settings.chroma_model_cache_path)
        alice.add("alice likes Python", owner_user_id="alice", tenant_id="t")
        results = bob.query("Python", owner_user_id="bob", tenant_id="t")
        assert results == []

    @pytest.mark.parametrize("text", [None, b"Python", 123])
    def test_non_string_query_returns_empty(self, tmp_path: Path, text: object) -> None:
        store = VectorStore(persist_dir=tmp_path / "vector", model_cache_path=settings.chroma_model_cache_path)
        store.add("alice likes Python", owner_user_id="alice", tenant_id="t")
        assert store.query(text, owner_user_id="alice", tenant_id="t") == []

    def test_legacy_metadata_is_migrated_for_local_queries(self, tmp_path: Path) -> None:
        import chromadb

        persist_dir = tmp_path / "legacy"
        client = chromadb.PersistentClient(path=str(persist_dir))
        collection = client.get_or_create_collection("memories")
        collection.add(documents=["old local fact"], ids=["legacy"], metadatas=[{"source": "agent"}])

        store = VectorStore(persist_dir=persist_dir, model_cache_path=settings.chroma_model_cache_path)
        results = store.query("old local fact", owner_user_id="local", tenant_id="default")

        assert len(results) == 1
        assert results[0]["text"] == "old local fact"
