from person_agents.runtime.state import SQLiteStateStore
from person_agents.security.confirmation import Confirmation, ConfirmationStore
from person_agents.security.context import RequestContext


def test_confirmation_survives_reopening_state_store(tmp_path):
    path = tmp_path / "state.db"
    confirmation = Confirmation(
        token="restart-token",
        owner_user_id="user",
        tenant_id="tenant",
        tool_name="filesystem",
        arguments={"operation": "write", "path": "a.txt"},
        expires_at=__import__("time").time() + 60,
    )

    first = ConfirmationStore(SQLiteStateStore(path))
    first.state_store.store_confirmation(confirmation, 60)

    second = ConfirmationStore(SQLiteStateStore(path))
    assert second.get("restart-token") is not None

    consumed = second.consume("restart-token")
    assert consumed is not None
    assert second.consume("restart-token") is None


def test_expired_confirmation_is_rejected(tmp_path):
    import time

    store = ConfirmationStore(SQLiteStateStore(tmp_path / "state.db"))
    confirmation = Confirmation(
        token="expired",
        owner_user_id="user",
        tenant_id="tenant",
        tool_name="filesystem",
        arguments={},
        expires_at=time.time() - 1,
    )
    store.state_store.store_confirmation(confirmation, 1)

    assert store.get("expired") is None
    assert store.consume("expired") is None


async def test_confirmation_rejects_cross_owner(tmp_path, monkeypatch):
    from person_agents.graph import tool_confirmation

    class Registry:
        def get(self, name):
            raise AssertionError("handler must not be reached")

    store = ConfirmationStore(SQLiteStateStore(tmp_path / "state.db"))
    store.create("owner", "tenant", "filesystem", {"operation": "read", "path": "a.txt"})
    monkeypatch.setattr("person_agents.graph.tool_confirmation._confirmations", store)

    confirmation = store.create("owner", "tenant", "filesystem", {"operation": "read", "path": "a.txt"})
    try:
        await tool_confirmation.confirm_pending_tool(
            confirmation.token,
            context=RequestContext(user_id="other", tenant_id="tenant", session_id="session"),
            registry=Registry(),
        )
    except PermissionError as exc:
        assert str(exc) == "Confirmation does not match request"
    else:
        raise AssertionError("cross-owner confirmation was accepted")
