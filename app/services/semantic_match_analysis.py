from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import LLMClient, LLMConfigurationError, LLMResponseError, extract_json_object
from app.models.entities import Job, Profile


@dataclass(frozen=True)
class SemanticMatchResult:
    applied: bool
    payload: dict[str, Any]
    metadata: dict[str, Any]
    warning: str | None = None


class SemanticMatchAnalysisService:
    """Grounded, selected-job fit analysis. Bulk retrieval remains model-free."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    async def analyze(
        self,
        db: Session,
        *,
        profile: Profile,
        job: Job,
        baseline: dict[str, Any],
    ) -> SemanticMatchResult:
        if not self.llm.available:
            raise LLMConfigurationError("岗位适配与差距分析需要可用的 LLM。")

        profile_source = self._profile_source(profile)
        jd_source = str(job.raw_jd_text or "").strip()
        retrieved = baseline.get("relevant_evidence") or []
        evidence_context = "\n".join(
            f"[{index}] {str(item.get('text') or '')[:1200]}"
            for index, item in enumerate(retrieved[:8], start=1)
        )
        alternative_groups = [
            group
            for group in (job.structured_jd_json or {}).get("alternative_skill_groups") or []
            if isinstance(group, dict)
        ]
        alternative_context = json.dumps(alternative_groups, ensure_ascii=False)
        system_prompt = """You assess candidate-job fit for a Chinese job seeker.
Use semantic meaning, not exact keyword overlap. A framework mentioned in work duties is not automatically a hard requirement.
Treat alternatives such as 'A or B' as one requirement. Do not infer that every example in an 'such as' list is mandatory.
Every matched item must contain a short verbatim jd_quote and resume_quote. Every gap must contain a verbatim jd_quote.
Return at most 6 matched items and 4 important gaps. Keep reasons concise.
Do not invent experience or turn planned learning into delivered experience. Return one strict JSON object only."""
        user_prompt = f"""Return this schema:
{{
  "fit_score": 0-100,
  "summary": "2-3 sentence Chinese conclusion",
  "matched": [{{"requirement": "semantic capability", "jd_quote": "verbatim JD fragment", "resume_quote": "verbatim resume fragment", "reason": "Chinese explanation"}}],
  "gaps": [{{"requirement": "important unmet requirement", "jd_quote": "verbatim JD fragment", "reason": "Chinese explanation"}}],
  "suggestions": ["specific next action"]
}}

Job title: {job.title}
JD SOURCE:
{jd_source[:8000]}

RESUME SOURCE:
{profile_source[:9000]}

RAG RETRIEVED RESUME EVIDENCE:
{evidence_context[:3500]}

KNOWN ALTERNATIVE REQUIREMENTS (satisfying the minimum means other options are not gaps):
{alternative_context[:2000]}
"""
        text = await self.llm.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=1100,
            response_format={"type": "json_object"},
            db=db,
            trace_name="matcher.semantic_fit",
        )
        parsed = extract_json_object(text)

        matched = self._ground_matched(parsed.get("matched"), jd_source, profile_source)
        qualification_source = "\n".join(
            str(item).strip()
            for item in (job.structured_jd_json or {}).get("qualifications") or []
            if str(item).strip()
        )
        gaps = self._ground_gaps(parsed.get("gaps"), jd_source, qualification_source)
        grounded_citation_count = len(matched) * 2 + len(gaps)
        gaps = [
            item
            for item in gaps
            if not self._conflicts_with_satisfied_alternative(
                item["requirement"],
                alternative_groups,
                profile_source,
            )
        ]
        gap_requirements = {self._normalize(item["requirement"]) for item in gaps}
        matched = [
            item
            for item in matched
            if self._normalize(item["requirement"]) not in gap_requirements
        ]
        requested_count = len(parsed.get("matched") or []) * 2 + len(parsed.get("gaps") or [])
        if requested_count == 0 or grounded_citation_count / requested_count < 0.75:
            raise LLMResponseError(
                "岗位适配分析未通过引用完整性门禁："
                f"citation_grounding_rate={grounded_citation_count / max(requested_count, 1):.4f}"
            )

        fit_score = self._score(parsed.get("fit_score"), baseline.get("overall_score", 0.0))
        summary_parts: list[str] = []
        if matched:
            summary_parts.append("已找到可验证的相关经历：" + "、".join(item["requirement"] for item in matched[:4]))
        if gaps:
            summary_parts.append("需要进一步核实或补充证据：" + "、".join(item["requirement"] for item in gaps[:4]))
        summary = "；".join(summary_parts) + ("。" if summary_parts else "")
        suggestions = [
            f"针对“{item['requirement']}”：{item['reason']}"
            for item in gaps
            if item.get("reason")
        ]
        payload = dict(baseline)
        payload["overall_score"] = fit_score
        payload["matched_skills"] = [item["requirement"] for item in matched]
        payload["missing_skills"] = [item["requirement"] for item in gaps]
        payload["suggestions"] = ([summary] if summary else []) + suggestions[:5]
        dimensions = dict(payload.get("dimension_scores") or {})
        dimensions["semantic_fit_judgement"] = fit_score
        payload["dimension_scores"] = dimensions
        return SemanticMatchResult(
            applied=True,
            payload=payload,
            metadata={
                "mode": "llm_semantic_grounded",
                "citation_grounding_rate": round(
                    grounded_citation_count / max(requested_count, 1), 4
                ),
                "matched_evidence": matched,
                "gap_evidence": gaps,
            },
        )

    def _ground_matched(
        self,
        rows: Any,
        jd_source: str,
        profile_source: str,
    ) -> list[dict[str, str]]:
        output: list[dict[str, str]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            jd_quote = str(row.get("jd_quote") or "").strip()
            resume_quote = str(row.get("resume_quote") or "").strip()
            requirement = str(row.get("requirement") or "").strip()
            if requirement and self._quote_in(jd_quote, jd_source) and self._quote_in(resume_quote, profile_source):
                output.append(
                    {
                        "requirement": requirement,
                        "jd_quote": jd_quote,
                        "resume_quote": resume_quote,
                        "reason": str(row.get("reason") or "").strip(),
                    }
                )
        return output[:8]

    def _ground_gaps(
        self,
        rows: Any,
        jd_source: str,
        qualification_source: str,
    ) -> list[dict[str, str]]:
        output: list[dict[str, str]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            jd_quote = str(row.get("jd_quote") or "").strip()
            requirement = str(row.get("requirement") or "").strip()
            if (
                requirement
                and qualification_source
                and self._quote_in(jd_quote, jd_source)
                and self._quote_in(jd_quote, qualification_source)
            ):
                output.append(
                    {
                        "requirement": requirement,
                        "jd_quote": jd_quote,
                        "reason": str(row.get("reason") or "").strip(),
                    }
                )
        return output[:8]

    @staticmethod
    def _quote_in(quote: str, source: str) -> bool:
        if len(quote.strip()) < 2:
            return False
        normalize = lambda value: re.sub(r"\s+", "", value).lower()  # noqa: E731
        return normalize(quote) in normalize(source)

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())

    def _conflicts_with_satisfied_alternative(
        self,
        requirement: str,
        groups: list[dict[str, Any]],
        profile_source: str,
    ) -> bool:
        requirement_norm = self._normalize(requirement)
        profile_norm = self._normalize(profile_source)
        for group in groups:
            options = [str(item).strip() for item in group.get("skills") or [] if str(item).strip()]
            minimum = max(1, int(group.get("min_required") or 1))
            mentioned_options = [
                option for option in options if self._normalize(option) in requirement_norm
            ]
            supported_options = [
                option for option in options if self._normalize(option) in profile_norm
            ]
            if mentioned_options and len(supported_options) >= minimum:
                return True
        return False

    @staticmethod
    def _score(value: Any, fallback: Any) -> float:
        try:
            return round(max(0.0, min(100.0, float(value))), 2)
        except (TypeError, ValueError):
            return round(float(fallback or 0.0), 2)

    @staticmethod
    def _profile_source(profile: Profile) -> str:
        structured = json.dumps(profile.structured_profile_json or {}, ensure_ascii=False, indent=2)
        return "\n".join(
            item
            for item in [str(profile.raw_resume_text or "").strip(), structured]
            if item
        )
