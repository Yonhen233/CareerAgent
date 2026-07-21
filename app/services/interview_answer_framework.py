from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models.entities import Job, Profile
from app.services.interview_references import InterviewReferenceService


class InterviewAnswerFrameworkService:
    """Normalize persisted interview answers without generating semantic content at read time."""

    CURRENT_VERSION = "interview_agentic_rag_v2"

    def normalize_question_sets(
        self,
        question_sets: list[dict[str, Any]] | None,
        *,
        profile: Profile,
        job: Job,
    ) -> list[dict[str, Any]]:
        del profile, job
        normalized = deepcopy(question_sets or [])
        for group in normalized:
            for question in group.get("questions") or []:
                self._normalize_question(question)
        return normalized

    def _normalize_question(self, question: dict[str, Any]) -> None:
        question["evidence_refs"] = self._normalize_evidence_refs(question.get("evidence_refs") or [])
        source, label = self._question_source(question)
        question.setdefault("question_generation_source", source)
        question.setdefault("question_generation_source_label", label)

        is_current = (
            question.get("reference_answer_source") == "agentic_rag_llm"
            and question.get("reference_answer_version") == self.CURRENT_VERSION
            and bool(str(question.get("reference_answer") or "").strip())
        )
        if is_current:
            question["requires_regeneration"] = False
            question["answer_framework"] = self._valid_framework(question.get("answer_framework"))
            return

        # Legacy rule-composed answers are not silently regenerated or presented as LLM/RAG output.
        question["requires_regeneration"] = True
        question["reference_answer"] = ""
        question["reference_answer_source"] = "legacy_requires_regeneration"
        question["reference_answer_source_label"] = "旧版面试包未经过 Agentic RAG 引用校验，请重新生成"
        question["reference_answer_version"] = ""
        question["reference_answer_basis"] = ""
        question["answer_framework"] = []
        question["answer_framework_source"] = "legacy_requires_regeneration"
        question["answer_framework_source_label"] = "旧版回答思路已停用，请重新生成面试包"

    def _normalize_evidence_refs(self, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in refs:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            source_url = str(item.get("source_url") or item.get("url") or "").strip()
            if source_url and InterviewReferenceService.is_valid_public_url(source_url):
                item["source_url"] = source_url
            else:
                item.pop("source_url", None)
                item.pop("url", None)
            key = str(item.get("evidence_id") or item.get("ref") or item.get("preview") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(item)
        return normalized

    def _question_source(self, question: dict[str, Any]) -> tuple[str, str]:
        source = str(question.get("source_perspective") or "")
        labels = {
            "source_backed_interview_experience": "已导入面经",
            "online_experience_research": "面经调研问题",
            "resume_project_evidence": "简历项目证据生成",
            "resume_project_stack": "简历技术栈生成",
            "llm_project_implementation": "LLM 根据项目与 JD 生成",
            "llm_foundation_drill": "LLM 根据岗位能力生成",
            "jd_technical_depth": "JD 技术要求生成",
            "jd_gap_drill": "JD 能力缺口生成",
            "general_interview": "通用面试主题生成",
        }
        if source == "source_backed_interview_experience":
            return "imported_interview_experience", labels[source]
        if source.startswith("llm_"):
            return "llm_generated", labels.get(source, "LLM 生成")
        return "structured_question_builder", labels.get(source, "系统根据岗位与简历生成")

    def _valid_framework(self, value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list):
            return []
        output: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            section = str(item.get("section") or "").strip()
            guidance = str(item.get("guidance") or "").strip()
            if section and guidance:
                output.append({"section": section, "guidance": guidance})
        return output[:6]
