from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills"

TASK_SKILL_MAPPING: dict[str, list[str]] = {
    "find_jobs_for_profile": [
        "resume_intake_and_structuring",
        "jd_structuring",
        "evidence_retrieval",
    ],
    "tailor_resume_for_job": [
        "evidence_retrieval",
        "fit_assessment",
        "resume_tailoring",
    ],
    "quick_apply": ["resume_tailoring", "application_packet"],
    "prepare_interview_for_job": [
        "evidence_retrieval",
        "fit_assessment",
        "interview_preparation",
    ],
    "full_career_flow": [
        "resume_intake_and_structuring",
        "jd_structuring",
        "evidence_retrieval",
        "fit_assessment",
        "resume_tailoring",
        "application_packet",
        "interview_preparation",
    ],
}


class SkillDefinitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentSkillSpec:
    name: str
    version: str
    status: str
    owner_subagent: str
    purpose: str
    trigger: str
    required_inputs: list[str]
    tools: list[str]
    context_policy: str
    output_contract: dict[str, str]
    forbidden_behaviors: list[str]
    success_criteria: list[str]
    failure_policy: str
    instructions: str
    source_path: str

    def as_dict(self, *, include_instructions: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        instructions = payload.pop("instructions")
        payload["instructions_loaded"] = include_instructions
        payload["instructions_chars"] = len(instructions)
        if include_instructions:
            payload["instructions"] = instructions
        return payload


class SkillRegistry:
    """Load versioned business capabilities from SKILL.md files."""

    REQUIRED_FIELDS = {
        "name",
        "version",
        "status",
        "owner_subagent",
        "purpose",
        "trigger",
        "required_inputs",
        "allowed_tools",
        "context_policy",
        "output_contract",
        "forbidden_behaviors",
        "success_criteria",
        "failure_policy",
    }

    def __init__(self, root: Path = SKILL_ROOT) -> None:
        self.root = root
        self._skills = self._load()

    def _load(self) -> dict[str, AgentSkillSpec]:
        if not self.root.exists():
            raise SkillDefinitionError(f"Skill directory does not exist: {self.root}")
        loaded: dict[str, AgentSkillSpec] = {}
        for path in sorted(self.root.glob("*/SKILL.md")):
            metadata, instructions = self._parse(path)
            missing = sorted(self.REQUIRED_FIELDS - set(metadata))
            if missing:
                raise SkillDefinitionError(f"{path} is missing metadata: {', '.join(missing)}")
            name = str(metadata["name"]).strip()
            if not name:
                raise SkillDefinitionError(f"{path} has an empty skill name.")
            if name in loaded:
                raise SkillDefinitionError(f"Duplicate skill name: {name}")
            relative_path = path.relative_to(self.root.parent).as_posix()
            loaded[name] = AgentSkillSpec(
                name=name,
                version=str(metadata["version"]).strip(),
                status=str(metadata["status"]).strip(),
                owner_subagent=str(metadata["owner_subagent"]).strip(),
                purpose=str(metadata["purpose"]).strip(),
                trigger=str(metadata["trigger"]).strip(),
                required_inputs=self._string_list(metadata["required_inputs"], path, "required_inputs"),
                tools=self._string_list(metadata["allowed_tools"], path, "allowed_tools"),
                context_policy=str(metadata["context_policy"]).strip(),
                output_contract={
                    str(key): str(value) for key, value in dict(metadata["output_contract"]).items()
                },
                forbidden_behaviors=self._string_list(
                    metadata["forbidden_behaviors"], path, "forbidden_behaviors"
                ),
                success_criteria=self._string_list(metadata["success_criteria"], path, "success_criteria"),
                failure_policy=str(metadata["failure_policy"]).strip(),
                instructions=instructions.strip(),
                source_path=relative_path,
            )
        if not loaded:
            raise SkillDefinitionError(f"No SKILL.md definitions found under {self.root}")
        return loaded

    def _parse(self, path: Path) -> tuple[dict[str, Any], str]:
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---\n"):
            raise SkillDefinitionError(f"{path} must start with YAML front matter.")
        try:
            _, front_matter, instructions = raw.split("---", 2)
        except ValueError as exc:
            raise SkillDefinitionError(f"{path} has invalid YAML front matter boundaries.") from exc
        metadata = yaml.safe_load(front_matter)
        if not isinstance(metadata, dict):
            raise SkillDefinitionError(f"{path} front matter must be a mapping.")
        if not instructions.strip():
            raise SkillDefinitionError(f"{path} must include skill instructions.")
        return metadata, instructions

    def _string_list(self, value: Any, path: Path, field_name: str) -> list[str]:
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            raise SkillDefinitionError(f"{path} field {field_name} must be a non-empty string list.")
        return [item.strip() for item in value]

    def list(self, *, include_deferred: bool = True) -> list[AgentSkillSpec]:
        rows = list(self._skills.values())
        return rows if include_deferred else [skill for skill in rows if skill.status == "active"]

    def get(self, name: str) -> AgentSkillSpec:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown Agent skill: {name}") from exc

    def for_task(self, task_type: str) -> list[AgentSkillSpec]:
        names = TASK_SKILL_MAPPING.get(task_type, [])
        return [self.get(name) for name in names]

    def allowed_tools_for_task(self, task_type: str) -> set[str]:
        return {tool for skill in self.for_task(task_type) for tool in skill.tools}

    def validate_tools(self, task_type: str, tool_names: list[str]) -> list[str]:
        allowed = self.allowed_tools_for_task(task_type)
        return sorted({name for name in tool_names if name not in allowed})

    def skills_for_tool(self, tool_name: str) -> list[str]:
        return sorted(skill.name for skill in self._skills.values() if tool_name in skill.tools)


@lru_cache(maxsize=1)
def get_skill_registry() -> SkillRegistry:
    return SkillRegistry()


# Compatibility export for callers that used the original in-code registry.
AGENT_SKILLS: list[AgentSkillSpec] = get_skill_registry().list(include_deferred=True)


def list_agent_skills(*, include_deferred: bool = True) -> list[dict[str, Any]]:
    return [
        skill.as_dict(include_instructions=False)
        for skill in get_skill_registry().list(include_deferred=include_deferred)
    ]


def get_agent_skill(name: str, *, include_instructions: bool = True) -> dict[str, Any]:
    return get_skill_registry().get(name).as_dict(include_instructions=include_instructions)


def active_skill_names_for_task(task_type: str) -> list[str]:
    return [skill.name for skill in get_skill_registry().for_task(task_type)]


def skill_contracts_for_task(task_type: str) -> list[dict[str, Any]]:
    return [
        skill.as_dict(include_instructions=False)
        for skill in get_skill_registry().for_task(task_type)
    ]


def validate_tool_permissions(task_type: str, tool_names: list[str]) -> list[str]:
    return get_skill_registry().validate_tools(task_type, tool_names)


def skill_names_for_tool(tool_name: str) -> list[str]:
    return get_skill_registry().skills_for_tool(tool_name)
