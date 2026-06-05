from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Job, MatchResult, Profile
from app.services.embedding_service import EmbeddingService, cosine_similarity, tokenize
from app.services.vector_index import SQLiteVectorIndex


def normalize_skill(skill: str) -> str:
    return skill.strip().lower()


def fuzzy_contains(needle: str, haystack_tokens: set[str], haystack_text: str) -> bool:
    needle_norm = normalize_skill(needle)
    if not needle_norm:
        return False
    if needle_norm in haystack_text:
        return True
    return any(SequenceMatcher(None, needle_norm, token).ratio() >= 0.88 for token in haystack_tokens)


class MatcherService:
    def __init__(self) -> None:
        self.vector_index = SQLiteVectorIndex()
        self.embedding_service = EmbeddingService()

    def build_match_payload(self, db: Session, profile: Profile, job: Job) -> dict[str, Any]:
        profile_data = profile.structured_profile_json or {}
        job_data = job.structured_jd_json or {}

        required = [str(x).strip() for x in job_data.get("required_skills", []) if str(x).strip()]
        preferred = [str(x).strip() for x in job_data.get("preferred_skills", []) if str(x).strip()]
        all_required = required or [str(x).strip() for x in job_data.get("keywords", [])[:12] if str(x).strip()]
        resume_text = f"{profile.raw_resume_text}\n{profile_data}".lower()
        resume_tokens = set(tokenize(resume_text))

        matched = [skill for skill in all_required if fuzzy_contains(skill, resume_tokens, resume_text)]
        missing = [skill for skill in all_required if skill not in matched]
        required_score = len(matched) / max(len(all_required), 1)

        semantic_score = self._semantic_similarity(profile.raw_resume_text, job.raw_jd_text)
        evidence = self.retrieve_evidence(db, profile.id, job, top_k=8)
        project_score = self._project_relevance(evidence)
        internship_score = self._internship_fit(profile, job)
        preference_score = self._preference_coverage(preferred, resume_tokens, resume_text)

        overall = (
            required_score * 0.38
            + semantic_score * 0.24
            + project_score * 0.22
            + internship_score * 0.08
            + preference_score * 0.08
        )
        dimension_scores = {
            "required_skill_coverage": round(required_score * 100, 2),
            "semantic_similarity": round(semantic_score * 100, 2),
            "evidence_relevance": round(project_score * 100, 2),
            "internship_fit": round(internship_score * 100, 2),
            "preferred_skill_coverage": round(preference_score * 100, 2),
        }
        return {
            "overall_score": round(overall * 100, 2),
            "dimension_scores": dimension_scores,
            "matched_skills": matched,
            "missing_skills": missing,
            "relevant_evidence": evidence,
            "suggestions": self._suggestions(missing, dimension_scores),
        }

    def create_match_result(self, db: Session, profile: Profile, job: Job) -> MatchResult:
        payload = self.build_match_payload(db, profile, job)
        result = MatchResult(
            profile_id=profile.id,
            job_id=job.id,
            overall_score=payload["overall_score"],
            dimension_scores_json=payload["dimension_scores"],
            matched_skills_json=payload["matched_skills"],
            missing_skills_json=payload["missing_skills"],
            relevant_evidence_json=payload["relevant_evidence"],
            suggestions_json=payload["suggestions"],
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        return result

    def retrieve_evidence(self, db: Session, profile_id: int, job: Job, top_k: int = 8) -> list[dict[str, Any]]:
        job_data = job.structured_jd_json or {}
        query_parts = [
            job.title,
            " ".join(job_data.get("required_skills", []) or []),
            " ".join(job_data.get("keywords", []) or []),
            " ".join(job_data.get("responsibilities", [])[:4] or []),
            job.raw_jd_text[:900],
        ]
        query = "\n".join(part for part in query_parts if part)
        return [chunk.as_dict() for chunk in self.vector_index.query_profile_chunks(db, profile_id, query, top_k=top_k)]

    def _semantic_similarity(self, resume_text: str, jd_text: str) -> float:
        embeddings = self.embedding_service.embed_texts([resume_text, jd_text])
        if len(embeddings.vectors) < 2:
            return 0.0
        left, right = embeddings.vectors
        return max(0.0, min(1.0, (cosine_similarity(left, right) + 1.0) / 2.0))

    def _project_relevance(self, evidence: list[dict[str, Any]]) -> float:
        if not evidence:
            return 0.35
        weighted = 0.0
        for item in evidence[:6]:
            base = float(item.get("score") or 0.0)
            if item.get("chunk_type") in {"project", "experience"}:
                base += 0.18
            weighted += max(0.0, min(base, 1.0))
        return max(0.0, min(weighted / min(len(evidence), 6), 1.0))

    def _internship_fit(self, profile: Profile, job: Job) -> float:
        text = f"{job.title} {job.job_type or ''} {job.raw_jd_text}".lower()
        if not any(token in text for token in ["intern", "internship", "实习"]):
            return 0.75
        profile_text = f"{profile.headline or ''} {profile.raw_resume_text}".lower()
        if any(token in profile_text for token in ["intern", "实习", "student", "university", "本科", "硕士"]):
            return 0.95
        return 0.72

    def _preference_coverage(self, preferred: list[str], resume_tokens: set[str], resume_text: str) -> float:
        if not preferred:
            return 0.7
        matched = [skill for skill in preferred if fuzzy_contains(skill, resume_tokens, resume_text)]
        return len(matched) / max(len(preferred), 1)

    def _suggestions(self, missing: list[str], dimensions: dict[str, float]) -> list[str]:
        suggestions: list[str] = []
        if missing:
            suggestions.append("补足或补证 JD 里的关键技能：" + "、".join(missing[:8]))
        if dimensions["evidence_relevance"] < 55:
            suggestions.append("把最相关的 Agent/RAG/后端项目提前，并写清工具链、闭环指标和落地效果。")
        if dimensions["semantic_similarity"] < 60:
            suggestions.append("在项目描述中自然覆盖岗位关键词，避免只罗列技术名词。")
        if not suggestions:
            suggestions.append("匹配度较高，建议重点压缩无关经历并强化可量化结果。")
        return suggestions
