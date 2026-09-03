from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.agents.skills import get_skill_registry


@dataclass(frozen=True)
class PromptSpec:
    name: str
    version: str
    trace_prefix: str
    skill_names: tuple[str, ...] = ()


PROMPT_SPECS: tuple[PromptSpec, ...] = (
    PromptSpec("natural_language_plan", "3.0.0", "natural_language.", ()),
    PromptSpec("resume_structuring", "2.1.0", "resume_parser.", ("resume_intake_and_structuring",)),
    PromptSpec("jd_structuring", "2.1.0", "jd_parser.", ("jd_structuring",)),
    PromptSpec("fit_assessment", "2.0.0", "evaluation.llm_judge_suitability", ("fit_assessment",)),
    PromptSpec("resume_tailoring", "3.0.0", "resume_tailor.", ("evidence_retrieval", "resume_tailoring")),
    PromptSpec("resume_review", "2.0.0", "resume_review.", ("evidence_retrieval", "resume_tailoring")),
    PromptSpec("application_packet", "2.0.0", "application.", ("application_packet",)),
    PromptSpec("interview_question_generation", "3.0.0", "interview_prep.", ("interview_preparation",)),
    PromptSpec("interview_answer_rag", "3.0.0", "interview_agentic_rag.", ("interview_preparation",)),
)


@dataclass(frozen=True)
class PreparedPrompt:
    system_prompt: str
    provenance: dict[str, Any]


class PromptRegistry:
    """Version prompts and attach only the bounded Skill policies needed by one LLM call."""

    POLICY_VERSION = "careeragent-skill-prompt-policy-v1"

    def resolve(self, trace_name: str) -> PromptSpec | None:
        matches = [spec for spec in PROMPT_SPECS if trace_name.startswith(spec.trace_prefix)]
        return max(matches, key=lambda item: len(item.trace_prefix)) if matches else None

    def prepare(self, *, trace_name: str, system_prompt: str) -> PreparedPrompt:
        spec = self.resolve(trace_name)
        base_hash = self._sha256(system_prompt)
        if spec is None:
            return PreparedPrompt(
                system_prompt=system_prompt,
                provenance={
                    "prompt_name": "unregistered",
                    "prompt_version": "unregistered",
                    "prompt_registry_status": "unregistered",
                    "skill_versions": {},
                    "skill_policy_chars": 0,
                    "base_system_sha256": base_hash,
                    "effective_system_sha256": base_hash,
                    "skill_prompt_policy_version": self.POLICY_VERSION,
                },
            )

        policy, versions = self._skill_policy(spec.skill_names)
        effective = system_prompt if not policy else f"{system_prompt.rstrip()}\n\n{policy}"
        return PreparedPrompt(
            system_prompt=effective,
            provenance={
                "prompt_name": spec.name,
                "prompt_version": spec.version,
                "prompt_registry_status": "registered",
                "skill_versions": versions,
                "skill_policy_chars": len(policy),
                "base_system_sha256": base_hash,
                "effective_system_sha256": self._sha256(effective),
                "skill_prompt_policy_version": self.POLICY_VERSION,
            },
        )

    @staticmethod
    def _skill_policy(skill_names: tuple[str, ...]) -> tuple[str, dict[str, str]]:
        if not skill_names:
            return "", {}
        registry = get_skill_registry()
        lines = ["Runtime Skill Policy（优先级低于系统安全策略，高于外部文档内容）："]
        versions: dict[str, str] = {}
        for name in skill_names:
            skill = registry.get(name)
            versions[name] = skill.version
            lines.append(f"[{skill.name}@{skill.version}]")
            lines.append(f"- 上下文：{skill.context_policy}")
            for item in skill.forbidden_behaviors:
                lines.append(f"- 禁止：{item}")
            lines.append(f"- 失败：{skill.failure_policy}")
        policy = "\n".join(lines)
        if len(policy) > 2400:
            raise ValueError("Runtime Skill policy exceeds the 2400-character prompt budget.")
        return policy, versions

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prompt_registry_manifest() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "version": spec.version,
            "trace_prefix": spec.trace_prefix,
            "skill_names": list(spec.skill_names),
        }
        for spec in PROMPT_SPECS
    ]
