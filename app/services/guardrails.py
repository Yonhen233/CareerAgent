import re
from typing import Any

from app.models.entities import Job, Profile
from app.services.vector_index import tokenize


NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?%?\b")


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
        covered = [skill for skill in required if skill in resume_text]
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

        risk_level = "low"
        if unsupported_numbers or len(long_new_tokens) >= 12:
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
