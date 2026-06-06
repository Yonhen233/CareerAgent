import re
from typing import Any

from app.models.entities import Job, Profile
from app.services.vector_index import tokenize


NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?%?\b")
GAP_OR_NEGATION_CUES = [
    "did not",
    "do not",
    "does not",
    "not implement",
    "not implemented",
    "not build",
    "not built",
    "no ",
    "without ",
    "lacks ",
    "lack ",
    "currently lack",
    "not have",
    "no direct",
    "eager to learn",
    "seeking to learn",
    "willing to learn",
    "currently learning",
    "计划学习",
    "希望学习",
    "没有",
    "未实现",
    "未交付",
    "缺少",
]

SKILL_ALIASES = {
    "a/b testing": ["a/b test", "a/b tests", "ab testing", "experiment analysis"],
    "model evaluation": ["evaluation", "evaluation dashboards", "metrics"],
    "evaluation": ["model evaluation", "evaluation dashboards", "metrics"],
    "metrics": ["metric", "metric definitions"],
    "feature store": ["feature store pipelines"],
}


class ResumeGuardrailService:
    def verify(self, *, profile: Profile, job: Job, resume_markdown: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        source_text = f"{profile.raw_resume_text}\n{profile.structured_profile_json}".lower()
        resume_text = resume_markdown.lower()
        unsupported_numbers = [
            number for number in sorted(set(NUMBER_RE.findall(resume_markdown))) if number.lower() not in source_text
        ]

        profile_tokens = set(tokenize(source_text))
        resume_tokens = set(tokenize(resume_text))
        long_new_tokens = [
            token
            for token in sorted(resume_tokens - profile_tokens)
            if len(token) >= 12 and not token.startswith(("http", "linkedin", "github"))
        ][:20]

        jd_data = job.structured_jd_json or {}
        required = [str(x).lower() for x in jd_data.get("required_skills", []) if str(x).strip()]
        covered = [skill for skill in required if self._has_positive_or_neutral_skill_context(resume_text, skill)]
        unsupported_required_skill_claims = [
            skill
            for skill in required
            if self._has_positive_or_neutral_skill_context(resume_text, skill)
            and not self._has_positive_or_neutral_skill_context(source_text, skill)
        ]
        unsupported_gap_skill_mentions = [
            skill
            for skill in required
            if self._has_gap_or_negated_skill_context(resume_text, skill)
            and not self._has_positive_or_neutral_skill_context(source_text, skill)
        ]
        keyword_coverage = len(covered) / max(len(required), 1)
        evidence_coverage = min(1.0, len(evidence) / 6.0)

        issues = []
        if unsupported_numbers:
            issues.append(
                {
                    "type": "unsupported_metric",
                    "message": "Generated resume contains numeric metrics not found in the source resume.",
                    "items": unsupported_numbers,
                }
            )
        if len(long_new_tokens) >= 8:
            issues.append(
                {
                    "type": "possible_new_claims",
                    "message": "Generated resume introduced many long tokens absent from source evidence.",
                    "items": long_new_tokens,
                }
            )
        if unsupported_required_skill_claims:
            issues.append(
                {
                    "type": "unsupported_required_skill_claim",
                    "message": "Generated resume presents JD-required skills that are not positively supported by the source resume.",
                    "items": unsupported_required_skill_claims,
                }
            )
        if unsupported_gap_skill_mentions:
            issues.append(
                {
                    "type": "missing_skill_in_resume_body",
                    "message": "Generated resume mentions unsupported JD-required skills as learning intent or gap disclosure; keep these in alignment notes instead of resume body.",
                    "items": unsupported_gap_skill_mentions,
                }
            )

        risk_level = "low"
        if unsupported_numbers or len(long_new_tokens) >= 12 or unsupported_required_skill_claims or unsupported_gap_skill_mentions:
            risk_level = "high"
        elif issues or keyword_coverage < 0.35:
            risk_level = "medium"

        return {
            "passed": risk_level != "high",
            "risk_level": risk_level,
            "issues": issues,
            "hallucination_count": len(unsupported_numbers) + max(0, len(long_new_tokens) - 8),
            "jd_keyword_coverage_score": round(keyword_coverage * 100, 2),
            "evidence_coverage_score": round(evidence_coverage * 100, 2),
            "covered_required_skills": covered,
        }

    def _has_positive_or_neutral_skill_context(self, text: str, skill: str) -> bool:
        sentences = self._sentences_with_skill(text, skill)
        if not sentences:
            return False
        return any(not self._has_gap_or_negation_cue(sentence) for sentence in sentences)

    def _has_gap_or_negated_skill_context(self, text: str, skill: str) -> bool:
        return any(self._has_gap_or_negation_cue(sentence) for sentence in self._sentences_with_skill(text, skill))

    def _sentences_with_skill(self, text: str, skill: str) -> list[str]:
        terms = self._skill_terms(skill)
        if not terms:
            return []
        normalized = re.sub(r"[\n;•]+", ". ", text.lower())
        sentences = [sentence.strip() for sentence in re.split(r"[.!?。！？]", normalized) if sentence.strip()]
        return [sentence for sentence in sentences if any(term in sentence for term in terms)]

    def _skill_terms(self, skill: str) -> list[str]:
        skill_norm = str(skill).strip().lower()
        if not skill_norm:
            return []
        return [skill_norm, *SKILL_ALIASES.get(skill_norm, [])]

    def _has_gap_or_negation_cue(self, sentence: str) -> bool:
        lowered = sentence.lower()
        return any(cue in lowered for cue in GAP_OR_NEGATION_CUES)
