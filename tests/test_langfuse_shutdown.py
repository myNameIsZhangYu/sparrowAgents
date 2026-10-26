from person_agents.observability import langfuse


def test_shutdown_langfuse_resets_cached_client():
    class Client:
        def __init__(self):
            self.shutdown_called = False

        def shutdown(self):
            self.shutdown_called = True

    client = Client()
    langfuse._client = client

    langfuse.shutdown_langfuse()

    assert client.shutdown_called is True
    assert langfuse._client is None
