from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SKILL_SCHEMA = {
    "type": "object",
    "required": ["name", "version", "description", "plan"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "version": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "plan": {"type": "array", "minItems": 1},
        "tools": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "object"},
        "success_evidence": {"type": "object"},
    },
    "additionalProperties": False,
}


class SkillStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _validate(self, skill: dict[str, Any]) -> None:
        import jsonschema

        jsonschema.validate(skill, SKILL_SCHEMA)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-_.]*", str(skill["name"])):
            raise ValueError("skill name must be slug-like")

    def save(self, skill: dict[str, Any]) -> Path:
        self._validate(skill)
        directory = self.root / str(skill["name"])
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{skill['version']}.json"
        if path.exists():
            raise FileExistsError(f"skill version already exists: {skill['version']}")
        path.write_text(json.dumps(skill, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def get(self, name: str, version: str | None = None) -> dict[str, Any] | None:
        directory = self.root / name
        if not directory.exists():
            return None
        path = directory / f"{version}.json" if version else max(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, default=None)
        if path is None or not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict[str, Any]]:
        skills = []
        for directory in self.root.iterdir() if self.root.exists() else []:
            skill = self.get(directory.name)
            if skill:
                skills.append(skill)
        return sorted(skills, key=lambda item: item["name"])

    def search(self, query: str) -> list[dict[str, Any]]:
        needle = query.lower()
        return [skill for skill in self.list() if needle in skill.get("description", "").lower() or needle in skill.get("name", "").lower()]
