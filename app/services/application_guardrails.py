from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.entities import Job, Profile, ResumeVersion


@dataclass(frozen=True)
class ClaimTerm:
    name: str
    patterns: tuple[str, ...]


CLAIM_TERMS = (
    ClaimTerm("Agent", (r"\bagents?\b", "智能体")),
    ClaimTerm("RAG", (r"\brag\b", "检索增强", "retrieval augmented generation")),
    ClaimTerm("FastAPI", (r"\bfastapi\b",)),
    ClaimTerm("SQLite", (r"\bsqlite\b",)),
    ClaimTerm("LLM", (r"\bllms?\b", "大模型", "大语言模型")),
    ClaimTerm("Python", (r"\bpython\b",)),
    ClaimTerm("SQL", (r"\bsql\b",)),
    ClaimTerm("React", (r"\breact\b",)),
    ClaimTerm("TypeScript", (r"\btypescript\b",)),
    ClaimTerm("CSS", (r"\bcss\b",)),
    ClaimTerm("Playwright", (r"\bplaywright\b",)),
    ClaimTerm("Airflow", (r"\bairflow\b",)),
    ClaimTerm("dbt", (r"\bdbt\b",)),
    ClaimTerm("Kafka", (r"\bkafka\b",)),
    ClaimTerm("Spark", (r"\bspark\b",)),
    ClaimTerm("PyTorch", (r"\bpytorch\b",)),
    ClaimTerm("MLflow", (r"\bmlflow\b",)),
    ClaimTerm("Kubernetes", (r"\bkubernetes\b", r"\bk8s\b")),
    ClaimTerm("LangGraph", (r"\blanggraph\b",)),
    ClaimTerm("MCP", (r"\bmcp\b",)),
    ClaimTerm("Guardrail", (r"\bguardrails?\b", "护栏", "安全校验")),
    ClaimTerm("Evaluation", (r"\bevaluation\b", r"\beval\b", "评测", "评估")),
    ClaimTerm("Prompt", (r"\bprompt\b", "提示词")),
    ClaimTerm("Recommendation", ("推荐算法", "推荐系统", r"\brecommendation\b")),
    ClaimTerm("Ranking", ("排序模型", "召回排序", r"\branking\b")),
    ClaimTerm("Feature Engineering", ("特征工程", r"feature engineering")),
    ClaimTerm("Accessibility", (r"\baccessibility\b", "可访问性")),
)

CLAIM_VERB_PATTERNS = (
    "已有",
    "具备",
    "熟悉",
    "掌握",
    "使用过",
    "负责",
    "主导",
    "建设",
    "构建",
    "实现",
    "落地",
    "交付",
    "维护",
    "经验",
    r"\bbuilt\b",
    r"\bimplemented\b",
    r"\bmaintained\b",
    r"\bdelivered\b",
    r"\bexperience\b",
)
NEGATIVE_SUPPORT_PATTERNS = (
    r"\bno\b",
    r"\bnot\b",
    r"\bwithout\b",
    r"\blacks?\b",
    r"\bmissing\b",
    "没有",
    "无",
    "未",
    "不具备",
    "不熟悉",
    "缺少",
)


class ApplicationPacketGuardrail:
    def validate(
        self,
        *,
        profile: Profile,
        job: Job,
        resume_version: ResumeVersion | None,
        cover_letter: str,
        outreach_message: str,
        checklist: list[str],
        automation_result: dict[str, Any],
    ) -> dict[str, Any]:
        supported_terms = self._supported_terms(profile, resume_version)
        text = "\n".join([cover_letter or "", outreach_message or ""])
        unsupported_claims = self._unsupported_claims(text, supported_terms)
        issues: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        if unsupported_claims:
            issues.append(
                {
                    "code": "unsupported_claims",
                    "message": "投递文案包含候选人材料中没有证据支持的能力声明。",
                    "terms": unsupported_claims,
                }
            )
        if not self._mentions_job(cover_letter, job):
            issues.append(
                {
                    "code": "cover_letter_missing_job_target",
                    "message": "求职信没有明确提到目标公司或岗位。",
                }
            )
        if not self._mentions_job(outreach_message, job):
            warnings.append(
                {
                    "code": "outreach_missing_job_target",
                    "message": "外联文案没有明确提到目标公司或岗位。",
                }
            )
        if len((cover_letter or "").strip()) < 60:
            warnings.append({"code": "cover_letter_too_short", "message": "求职信过短，可能缺少上下文。"})
        if len((outreach_message or "").strip()) < 30:
            warnings.append({"code": "outreach_too_short", "message": "外联文案过短，可能不可直接使用。"})
        if not self._has_manual_confirmation(checklist, automation_result):
            issues.append(
                {
                    "code": "missing_manual_confirmation",
                    "message": "投递包没有保留提交前人工确认边界。",
                }
            )
        if not job.apply_url:
            warnings.append({"code": "missing_apply_url", "message": "岗位缺少投递链接，需要用户手动补充。"})

        risk_level = "high" if issues else "medium" if warnings else "low"
        return {
            "passed": not issues,
            "risk_level": risk_level,
            "issues": issues,
            "warnings": warnings,
            "supported_claim_terms": sorted(supported_terms),
            "checked_fields": ["cover_letter", "outreach_message", "checklist", "automation_result"],
        }

    def _supported_terms(self, profile: Profile, resume_version: ResumeVersion | None) -> set[str]:
        support_text = self._profile_support_text(profile)
        if resume_version is not None:
            support_text = "\n".join([support_text, resume_version.tailored_resume_markdown or ""])
        return {term.name for term in CLAIM_TERMS if self._has_positive_support(support_text, term)}

    def _profile_support_text(self, profile: Profile) -> str:
        structured = profile.structured_profile_json or {}
        return "\n".join(
            [
                profile.raw_resume_text or "",
                str(structured.get("skills") or ""),
                str(structured.get("projects") or ""),
                str(structured.get("work_experience") or ""),
                str(structured.get("raw_text") or ""),
            ]
        ).lower()

    def _unsupported_claims(self, text: str, supported_terms: set[str]) -> list[str]:
        unsupported: list[str] = []
        for sentence in self._sentences(text):
            if not self._has_claim_verb(sentence):
                continue
            for term in CLAIM_TERMS:
                if term.name in supported_terms:
                    continue
                if self._contains_any_pattern(sentence, term.patterns):
                    unsupported.append(term.name)
        return sorted(set(unsupported))

    def _has_positive_support(self, support_text: str, term: ClaimTerm) -> bool:
        for sentence in self._sentences(support_text):
            if not self._contains_any_pattern(sentence, term.patterns):
                continue
            if self._contains_any_pattern(sentence, NEGATIVE_SUPPORT_PATTERNS):
                continue
            return True
        return False

    def _sentences(self, text: str) -> list[str]:
        return [item.strip().lower() for item in re.split(r"[。！？!?；;\n]+", text or "") if item.strip()]

    def _has_claim_verb(self, text: str) -> bool:
        return self._contains_any_pattern(text, CLAIM_VERB_PATTERNS)

    def _mentions_job(self, text: str, job: Job) -> bool:
        lowered = (text or "").lower()
        title_tokens = [token for token in re.split(r"[\s,/|;:()（）\-]+", (job.title or "").lower()) if len(token) >= 2]
        company = (job.company or "").lower()
        return bool((company and company in lowered) or any(token in lowered for token in title_tokens[:4]))

    def _has_manual_confirmation(self, checklist: list[str], automation_result: dict[str, Any]) -> bool:
        checklist_text = " ".join(str(item) for item in checklist)
        mode = str((automation_result or {}).get("mode") or "")
        final_submission = str((automation_result or {}).get("final_submission") or "")
        return (
            ("人工确认" in checklist_text or "提交前" in checklist_text)
            and mode == "manual_confirm_required"
            and final_submission == "user_confirmed_only"
        )

    def _contains_any_pattern(self, text: str, patterns: tuple[str, ...]) -> bool:
        lowered = (text or "").lower()
        return any(re.search(pattern, lowered) is not None for pattern in patterns)
