from person_agents.runtime.llm_settings import (
    decrypt_api_key,
    save_llm_settings,
)


async def test_llm_settings_roundtrip(monkeypatch, tmp_path):
    record = save_llm_settings(
        "http://127.0.0.1:8788/v1",
        "glm-test",
        "secret-key",
        db_path=tmp_path / "runtime.db",
        runtime_secret="unit-test-secret",
    )
    assert record.openai_model == "glm-test"
    assert decrypt_api_key(record, runtime_secret="unit-test-secret") == "secret-key"
