from person_agents.skills.skill_store import SkillStore, derive_skill_from_plan


def valid_skill():
    return {
        "name": "write-python-file",
        "version": "1.0.0",
        "description": "Create a Python file and verify it",
        "plan": [
            {"action": "write file", "tool": "filesystem", "arguments": {}},
            {"action": "read file", "tool": "filesystem", "arguments": {}},
        ],
        "tools": ["filesystem"],
    }


def test_skill_store_ranks_matches_by_term_frequency(tmp_path):
    store = SkillStore(tmp_path)
    store.save(valid_skill())
    store.save({**valid_skill(), "name": "python-tests", "description": "run python tests"})

    matches = store.search_ranked("python file python")

    assert matches[0]["name"] == "write-python-file"
    assert matches[0]["score"] > matches[1]["score"]


def test_derive_skill_from_verified_plan(tmp_path):
    store = SkillStore(tmp_path)
    plan = [{"action": "write file", "tool": "filesystem", "arguments": {}}]
    result = derive_skill_from_plan("Create a Python file", plan, store=store, version="1.0.0")

    assert result is not None
    assert result["name"] == "create-a-python-file-3fd5c3d5"
    assert store.get(result["name"])["description"] == "Create a Python file"


def test_derive_skill_supports_unicode_and_unique_objectives(tmp_path):
    store = SkillStore(tmp_path)
    plan = [{"action": "创建文件", "tool": "filesystem", "arguments": {}}]

    chinese = derive_skill_from_plan("创建Python文件", plan, store=store, version="1.0.0")
    second = derive_skill_from_plan("编写Python文件", plan, store=store, version="1.0.0")
    pure_chinese = derive_skill_from_plan("创建文件", plan, store=store, version="1.0.0")

    assert chinese["name"] != second["name"]
    assert pure_chinese["name"] != ""
    for skill in (chinese, second, pure_chinese):
        assert store.get(skill["name"]) is not None
