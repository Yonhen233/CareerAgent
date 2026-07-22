from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.entities import Job, Profile, ResumeVersion
from app.services.embedding_service import EmbeddingService, cosine_similarity
from app.services.evidence_grounding import EvidenceGroundingService


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
    ClaimTerm("Accessibility", (r"\baccessibility\b", r"\ba11y\b", "可访问性", "无障碍")),
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
    r"(?:^|[\s，,。；;：:])无(?:相关|任何|此类|实际|直接|生产|项目|经验|能力|技能|经历|实现|交付)",
    r"未(?:实现|交付|使用|掌握|参与|负责|构建|开发|落地|完成|做过|具备)",
    "不具备",
    "不熟悉",
    "缺少",
)

OUTCOME_SEMANTIC_GROUPS: dict[str, tuple[str, ...]] = {
    "reliability": ("可靠", "稳定", r"\breliab", r"\bstab"),
    "performance": ("性能", "延迟", "吞吐", r"\bperformance\b", r"\blatency\b", r"\bthroughput\b"),
    "accuracy": ("准确", "精度", "召回", r"\baccuracy\b", r"\bprecision\b", r"\brecall\b"),
    "efficiency": ("效率", "提效", r"\befficien"),
    "cost": ("成本", "费用", r"\bcosts?\b"),
    "reuse": ("复用", r"\breus(?:e|able|ability)\b"),
    "coverage": ("覆盖", r"\bcoverage\b"),
}


class ApplicationPacketGuardrail:
    def __init__(self, *, embedding_service: EmbeddingService | None = None) -> None:
        self.embedding_service = embedding_service or EmbeddingService()

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
        trusted_fact_sources = self._profile_structured_fact_sources(profile)
        text = "\n".join([cover_letter or "", outreach_message or ""])
        unsupported_claims = self._unsupported_claims(text, supported_terms)
        support_sources = [self._profile_support_text(profile)]
        if resume_version is not None:
            support_sources.append(resume_version.tailored_resume_markdown or "")
        grounding = EvidenceGroundingService()
        semantic_grounding = grounding.evaluate_generated_claims(text, support_sources, threshold=0.12)
        semantic_grounding = self._recover_multilingual_grounding(
            semantic_grounding,
            support_sources=support_sources,
            grounding=grounding,
            supported_terms=supported_terms,
            trusted_fact_sources=trusted_fact_sources,
        )
        unsupported_numbers = grounding.unsupported_numbers(text, support_sources)
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
        if not semantic_grounding.get("passed"):
            issues.append(
                {
                    "code": "unsupported_evidence_claims",
                    "message": "投递文案包含无法回指到候选人材料的事实性陈述。",
                    "claims": [
                        item.get("claim") for item in semantic_grounding.get("unsupported_claims") or []
                    ],
                }
            )
        if unsupported_numbers:
            issues.append(
                {
                    "code": "unsupported_metrics",
                    "message": "投递文案包含候选人材料中没有出现的数字或指标。",
                    "numbers": unsupported_numbers,
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
            "semantic_claim_grounding": semantic_grounding,
            "unsupported_numbers": unsupported_numbers,
            "checked_fields": ["cover_letter", "outreach_message", "checklist", "automation_result"],
        }

    def _recover_multilingual_grounding(
        self,
        report: dict[str, Any],
        *,
        support_sources: list[str],
        grounding: EvidenceGroundingService,
        supported_terms: set[str],
        trusted_fact_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        unsupported = list(report.get("unsupported_claims") or [])
        if not unsupported:
            return report
        source_snippet_candidates: list[str] = []
        for source in support_sources:
            sentences = [item[:500] for item in grounding.sentences(source) if len(item.strip()) >= 8]
            source_snippet_candidates.extend(sentences)
            source_snippet_candidates.extend(
                f"{left} {right}"[:900]
                for left, right in zip(sentences, sentences[1:], strict=False)
            )
        trusted_facts = [str(item).strip() for item in trusted_fact_sources or [] if str(item).strip()]
        source_snippets = list(dict.fromkeys([*trusted_facts, *source_snippet_candidates]))[:120]
        if not source_snippets:
            return report
        claims = [str(item.get("claim") or "").strip() for item in unsupported]
        try:
            embeddings = self.embedding_service.embed_texts([*claims, *source_snippets])
        except Exception as exc:  # noqa: BLE001
            return {**report, "embedding_error": f"{type(exc).__name__}: {exc}"}
        claim_vectors = embeddings.vectors[: len(claims)]
        source_vectors = embeddings.vectors[len(claims) :]
        embedding_matches: dict[str, dict[str, Any]] = {}
        recovered: dict[str, dict[str, Any]] = {}
        for claim, claim_vector in zip(claims, claim_vectors, strict=False):
            scored_sources = [
                (cosine_similarity(claim_vector, source_vector), source)
                for source, source_vector in zip(source_snippets, source_vectors, strict=False)
            ]
            best, best_source = max(scored_sources, default=(0.0, ""), key=lambda item: item[0])
            polarity_consistent = self._is_negative_claim(claim) == self._is_negative_claim(best_source)
            outcome_consistent = self._outcome_semantics_consistent(claim, best_source)
            claim_terms = self._claim_terms(claim)
            taxonomy_corroborated = bool(claim_terms) and claim_terms.issubset(supported_terms)
            claim_outcome_groups = self._outcome_semantic_groups(claim)
            source_outcome_groups = self._outcome_semantic_groups(best_source)
            trusted_fact_corroborated = bool(claim_outcome_groups) and any(
                max(
                    grounding.support_score(best_source, fact),
                    grounding.support_score(fact, best_source),
                )
                >= 0.8
                for fact in trusted_facts
            )
            embedding_matches[claim] = {
                "score": round(best, 4),
                "source_preview": best_source[:240],
                "polarity_consistent": polarity_consistent,
                "outcome_semantics_consistent": outcome_consistent,
                "claim_terms": sorted(claim_terms),
                "taxonomy_corroborated": taxonomy_corroborated,
                "claim_outcome_groups": sorted(claim_outcome_groups),
                "source_outcome_groups": sorted(source_outcome_groups),
                "trusted_fact_corroborated": trusted_fact_corroborated,
            }
            if best >= 0.70 and polarity_consistent and outcome_consistent:
                recovered[claim] = {"score": round(best, 4), "method": "multilingual_embedding"}
            elif best >= 0.65 and polarity_consistent and outcome_consistent and taxonomy_corroborated:
                recovered[claim] = {
                    "score": round(best, 4),
                    "method": "multilingual_embedding_taxonomy_corroborated",
                }
            elif best >= 0.65 and polarity_consistent and outcome_consistent and trusted_fact_corroborated:
                recovered[claim] = {
                    "score": round(best, 4),
                    "method": "multilingual_embedding_structured_fact_corroborated",
                }

        results = []
        for item in report.get("results") or []:
            claim = str(item.get("claim") or "")
            recovery = recovered.get(claim)
            match = embedding_matches.get(claim) or {}
            results.append(
                {
                    **item,
                    "lexical_support_score": item.get("support_score"),
                    "embedding_support_score": match.get("score"),
                    "embedding_source_preview": match.get("source_preview"),
                    "embedding_polarity_consistent": match.get("polarity_consistent"),
                    "embedding_outcome_semantics_consistent": match.get("outcome_semantics_consistent"),
                    "embedding_claim_terms": match.get("claim_terms"),
                    "embedding_taxonomy_corroborated": match.get("taxonomy_corroborated"),
                    "embedding_claim_outcome_groups": match.get("claim_outcome_groups"),
                    "embedding_source_outcome_groups": match.get("source_outcome_groups"),
                    "embedding_trusted_fact_corroborated": match.get("trusted_fact_corroborated"),
                    "support_method": recovery.get("method") if recovery else "lexical",
                    "supported": bool(item.get("supported")) or recovery is not None,
                }
            )
        unsupported_after = [item for item in results if not item.get("supported")]
        supported_count = len(results) - len(unsupported_after)
        return {
            **report,
            "passed": not unsupported_after,
            "supported_claim_count": supported_count,
            "grounding_rate": round(supported_count / max(len(results), 1), 4),
            "unsupported_claims": unsupported_after,
            "results": results,
            "embedding": embeddings.info(),
            "embedding_threshold": 0.70,
            "embedding_corroborated_threshold": 0.65,
        }

    def _claim_terms(self, text: str) -> set[str]:
        return {
            term.name
            for term in CLAIM_TERMS
            if any(re.search(pattern, text or "", flags=re.IGNORECASE) for pattern in term.patterns)
        }

    def _is_negative_claim(self, text: str) -> bool:
        return self._contains_any_pattern(text, NEGATIVE_SUPPORT_PATTERNS)

    def _outcome_semantics_consistent(self, claim: str, source: str) -> bool:
        claim_groups = self._outcome_semantic_groups(claim)
        if not claim_groups:
            return True
        source_groups = self._outcome_semantic_groups(source)
        return claim_groups.issubset(source_groups)

    def _outcome_semantic_groups(self, text: str) -> set[str]:
        return {
            name
            for name, patterns in OUTCOME_SEMANTIC_GROUPS.items()
            if self._contains_any_pattern(text, patterns)
        }

    def _profile_structured_fact_sources(self, profile: Profile) -> list[str]:
        structured = profile.structured_profile_json or {}
        facts: list[str] = []
        for field in ("projects", "work_experience", "campus_experience"):
            for item in structured.get(field) or []:
                if not isinstance(item, dict):
                    continue
                for key in ("description", "impact", "details"):
                    value = str(item.get(key) or "").strip()
                    if value:
                        facts.append(value)
        return list(dict.fromkeys(facts))

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
            clauses = [
                item.strip()
                for item in re.split(r"[,，]|(?:\band\b)|并且|同时|以及", sentence)
                if item.strip()
            ]
            for clause in clauses:
                if not self._has_claim_verb(clause):
                    continue
                for term in CLAIM_TERMS:
                    if term.name in supported_terms:
                        continue
                    if self._contains_any_pattern(clause, term.patterns):
                        if self._contains_any_pattern(clause, NEGATIVE_SUPPORT_PATTERNS):
                            continue
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
