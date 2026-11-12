import jsonschema
import pytest

from person_agents.skills.skill_store import SkillStore


def valid_skill():
    return {
        "name": "write-python-file",
        "version": "1.0.0",
        "description": "Create a Python file and verify it",
        "plan": [
            {"action": "write file", "tool": "filesystem", "arguments": {"operation": "write"}},
            {"action": "read file", "tool": "filesystem", "arguments": {"operation": "read"}},
        ],
        "tools": ["filesystem"],
        "constraints": {"requires_confirmation": True},
        "success_evidence": {"exists": True},
    }


def test_skill_store_saves_versions_and_latest(tmp_path):
    store = SkillStore(tmp_path)
    skill = valid_skill()
    store.save(skill)
    updated = {**skill, "version": "1.1.0", "description": "updated"}
    store.save(updated)

    assert store.get("write-python-file")["version"] == "1.1.0"
    assert store.get("write-python-file", "1.0.0")["description"] == "Create a Python file and verify it"
    with pytest.raises(FileExistsError):
        store.save(updated)


def test_skill_store_validates_contract(tmp_path):
    store = SkillStore(tmp_path)
    with pytest.raises(jsonschema.ValidationError):
        store.save({"name": "bad"})
    with pytest.raises(ValueError, match="slug-like"):
        store.save({**valid_skill(), "name": "Bad Skill"})


def test_skill_store_search(tmp_path):
    store = SkillStore(tmp_path)
    skill = valid_skill()
    store.save(skill)
    store.save({**skill, "name": "database-backup", "description": "Back up PostgreSQL"})

    assert store.search("python")[0]["name"] == "write-python-file"
    assert store.search("backup")[0]["name"] == "database-backup"
    assert store.search("not-found") == []
