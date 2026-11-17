from __future__ import annotations

import hashlib
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

    def search_ranked(self, query: str, *, limit: int = 3) -> list[dict[str, Any]]:
        terms = [term for term in re.findall(r"[a-z0-9_]+", query.lower()) if len(term) > 1]
        if not terms:
            return []
        scored: list[tuple[float, dict[str, Any]]] = []
        for skill in self.list():
            haystack = " ".join([
                skill.get("name", ""),
                skill.get("description", ""),
                " ".join(skill.get("tools", [])),
            ]).lower()
            score = sum(haystack.count(term) for term in set(terms))
            if score:
                scored.append((float(score), skill))
        scored.sort(key=lambda item: (-item[0], item[1]["name"]))
        return [{**skill, "score": score} for score, skill in scored[:max(1, limit)]]


def derive_skill_from_plan(
    objective: str,
    plan: list[dict[str, Any]],
    *,
    store: SkillStore,
    version: str = "1.0.0",
) -> dict[str, Any] | None:
    executable_steps = [step for step in plan if step.get("tool") and step.get("tool") != "none"]
    if not objective.strip() or not executable_steps:
        return None
    normalized = re.sub(r"\s+", "-", objective.lower()).strip("-")
    encoded = normalized.encode("unicode_escape").decode("ascii")
    slug = re.sub(r"[^a-z0-9-]+", "-", encoded.lower()).strip("-")
    if not slug:
        slug = "skill"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
    slug = f"{slug}-{digest}"
    skill = {
        "name": slug,
        "version": version,
        "description": objective,
        "plan": [
            {
                "action": step.get("action", ""),
                "tool": step["tool"],
                "arguments": step.get("arguments", {}),
            }
            for step in executable_steps
        ],
        "tools": sorted({step["tool"] for step in executable_steps}),
    }
    store.save(skill)
    return skill
