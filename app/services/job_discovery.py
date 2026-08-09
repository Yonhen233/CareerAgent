from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Job, JobSearchResult, JobSearchSession, Profile
from app.models.schemas import JobDiscoveryRequest
from app.services.job_relevance import is_internship_like_posting, score_job_posting
from app.services.job_search import JobSearchService
from app.services.matcher import MatcherService
from app.services.reranker import RerankerService
from app.services.vector_index import SQLiteVectorIndex
from app.services.retrieval_quality import (
    RetrievalQualityError,
    RetrievalQualityService,
    retrieval_failure_message,
)


@dataclass
class DiscoveryCandidate:
    job: Job
    retrieval_score: float
    rule_score: float
    semantic_score: float
    reasons: list[str]
    rerank: dict[str, Any]
    match_result_id: int | None = None
    match_score: float | None = None
    matched_skills: list[str] | None = None
    missing_skills: list[str] | None = None
    final_score: float = 0.0


class JobDiscoveryService:
    def __init__(
        self,
        *,
        job_search: JobSearchService | None = None,
        matcher: MatcherService | None = None,
        vector_index: SQLiteVectorIndex | None = None,
        reranker: RerankerService | None = None,
    ) -> None:
        self.settings = get_settings()
        self.job_search = job_search or JobSearchService()
        self.matcher = matcher or MatcherService()
        self.vector_index = vector_index or SQLiteVectorIndex()
        self.reranker = reranker or RerankerService()
        self.retrieval_quality = RetrievalQualityService(self.settings)

    async def discover(
        self,
        db: Session,
        payload: JobDiscoveryRequest,
        *,
        tenant_id: str | None = None,
    ) -> JobSearchSession:
        profile = self._load_profile(db, payload.profile_id, tenant_id=tenant_id)
        preference = (payload.preference_text or "").strip()
        resolved_query = self._resolved_query(preference, profile)
        location = (payload.location or "").strip() or self._profile_location(profile)
        input_mode = self._input_mode(preference, profile)
        session = JobSearchSession(
            tenant_id=tenant_id,
            profile_id=profile.id if profile else None,
            input_mode=input_mode,
            preference_text=preference or None,
            resolved_query=resolved_query,
            location=location,
            internship_only=payload.internship_only,
            source_mode=payload.source_mode,
            status="running",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        source_errors: dict[str, str] = {}
        retrieval_quality: dict[str, Any] = {}
        try:
            if payload.source_mode in {"live", "hybrid"}:
                live_jobs, source_errors = await self.job_search.search(
                    db,
                    query=preference or self._external_query(profile),
                    location=location,
                    internship_only=payload.internship_only,
                    limit=max(payload.limit, 20),
                    sources=payload.sources,
                    store_results=True,
                )
                if self.settings.rbac_enabled:
                    for job in live_jobs:
                        job.tenant_id = tenant_id
                        db.add(job)
                    db.commit()
            candidates, retrieval_quality = self._retrieve_candidates(
                db,
                query=resolved_query,
                location=location,
                internship_only=payload.internship_only,
                tenant_id=tenant_id,
                limit=payload.limit,
            )
            session.retrieval_quality_json = retrieval_quality
            if not retrieval_quality.get("passed"):
                raise RetrievalQualityError(
                    retrieval_failure_message(retrieval_quality),
                    report=retrieval_quality,
                )
            if profile:
                self._attach_matches(db, profile, candidates)
            self._persist_results(db, session, candidates[: payload.limit])
            session.status = "completed"
            session.source_errors_json = source_errors
            session.result_count = min(len(candidates), payload.limit)
            db.commit()
            db.refresh(session)
            return session
        except Exception:
            db.rollback()
            stored = db.query(JobSearchSession).filter(JobSearchSession.id == session.id).first()
            if stored is not None:
                stored.status = "failed"
                stored.source_errors_json = source_errors
                stored.retrieval_quality_json = retrieval_quality
                db.commit()
            raise

    def get_session(
        self,
        db: Session,
        session_id: int,
        *,
        tenant_id: str | None = None,
    ) -> JobSearchSession | None:
        query = db.query(JobSearchSession).filter(JobSearchSession.id == session_id)
        if self.settings.rbac_enabled:
            query = query.filter(JobSearchSession.tenant_id == tenant_id)
        return query.first()

    def _retrieve_candidates(
        self,
        db: Session,
        *,
        query: str,
        location: str | None,
        internship_only: bool,
        tenant_id: str | None,
        limit: int,
    ) -> tuple[list[DiscoveryCandidate], dict[str, Any]]:
        rows_query = db.query(Job)
        if self.settings.rbac_enabled:
            rows_query = rows_query.filter(Job.tenant_id == tenant_id)
        jobs = rows_query.order_by(Job.updated_at.desc()).limit(800).all()
        if internship_only:
            jobs = [job for job in jobs if is_internship_like_posting(job)]
        if location:
            location_terms = [item.strip().lower() for item in location.replace("、", "/").split("/") if item.strip()]
            jobs = [
                job
                for job in jobs
                if not location_terms
                or any(term in (job.location or "").lower() for term in location_terms)
                or "远程" in (job.location or "")
            ]
        if not jobs:
            quality = self.retrieval_quality.assess(query, [], min_evidence_chunks=1)
            return [], quality

        lightweight_candidates: list[tuple[Job, Any]] = [
            (job, score_job_posting(job, query)) for job in jobs
        ]
        lightweight_candidates.sort(
            key=lambda item: (item[1].score, item[0].updated_at),
            reverse=True,
        )
        candidate_pool_size = max(12, min(80, limit * 4))
        lightweight_candidates = lightweight_candidates[:candidate_pool_size]
        job_ids = {job.id for job, _ in lightweight_candidates}
        chunks = self.vector_index.query_job_corpus(
            db,
            query,
            job_ids=job_ids,
            top_k=max(40, limit * 4),
            rerank=False,
        )
        semantic_by_job: dict[int, float] = {}
        for chunk in chunks:
            job_id = int((chunk.metadata or {}).get("job_id") or 0)
            if job_id:
                semantic_by_job[job_id] = max(semantic_by_job.get(job_id, 0.0), float(chunk.score))

        raw_candidates: list[dict[str, Any]] = []
        for job, relevance in lightweight_candidates:
            rule_norm = self._clamp((relevance.score + 8.0) / 28.0)
            semantic = self._clamp(semantic_by_job.get(job.id, 0.0))
            retrieval_score = round(semantic * 0.58 + rule_norm * 0.42, 6)
            raw_candidates.append(
                {
                    "job": job,
                    "text": self._job_text(job),
                    "chunk_type": "job",
                    "score": retrieval_score,
                    "metadata": {
                        "rule_score": relevance.score,
                        "semantic_score": semantic,
                        "reasons": relevance.reasons,
                    },
                }
            )
        raw_candidates.sort(key=lambda item: item["score"], reverse=True)
        reranked = self.reranker.rerank_dicts(
            query,
            raw_candidates[: max(30, limit * 3)],
            top_k=max(30, limit * 3),
        )
        quality = self.retrieval_quality.assess(query, reranked, min_evidence_chunks=1)
        quality["query_strategy"] = {
            "name": "metadata_filter_then_hybrid_retrieval_and_rerank",
            "candidate_pool_size": len(raw_candidates),
        }
        candidates = [
            DiscoveryCandidate(
                job=item["job"],
                retrieval_score=round(float(item["score"]) * 100, 2),
                rule_score=float(item["metadata"].get("rule_score") or 0.0),
                semantic_score=float(item["metadata"].get("semantic_score") or 0.0),
                reasons=list(item["metadata"].get("reasons") or []),
                rerank=dict(item.get("metadata", {}).get("rerank") or {}),
                final_score=round(float(item["score"]) * 100, 2),
            )
            for item in reranked
        ]
        return candidates, quality

    def _attach_matches(self, db: Session, profile: Profile, candidates: list[DiscoveryCandidate]) -> None:
        for candidate in candidates:
            match = self.matcher.create_match_result(db, profile, candidate.job)
            candidate.match_result_id = match.id
            candidate.match_score = round(float(match.overall_score), 2)
            candidate.matched_skills = list(match.matched_skills_json or [])
            candidate.missing_skills = list(match.missing_skills_json or [])
            candidate.final_score = round(candidate.retrieval_score * 0.45 + candidate.match_score * 0.55, 2)
        candidates.sort(key=lambda item: item.final_score, reverse=True)

    def _persist_results(
        self,
        db: Session,
        session: JobSearchSession,
        candidates: list[DiscoveryCandidate],
    ) -> None:
        for rank, candidate in enumerate(candidates, start=1):
            db.add(
                JobSearchResult(
                    session_id=session.id,
                    job_id=candidate.job.id,
                    match_result_id=candidate.match_result_id,
                    rank=rank,
                    retrieval_score=candidate.retrieval_score,
                    match_score=candidate.match_score,
                    final_score=candidate.final_score,
                    reason_json={
                        "relevance_reasons": candidate.reasons,
                        "rule_score": candidate.rule_score,
                        "semantic_score": candidate.semantic_score,
                        "rerank": candidate.rerank,
                        "matched_skills": candidate.matched_skills or [],
                        "missing_skills": candidate.missing_skills or [],
                    },
                )
            )
        db.commit()

    def _load_profile(
        self,
        db: Session,
        profile_id: int | None,
        *,
        tenant_id: str | None,
    ) -> Profile | None:
        if not profile_id:
            return None
        query = db.query(Profile).filter(Profile.id == profile_id)
        if self.settings.rbac_enabled:
            query = query.filter(Profile.tenant_id == tenant_id)
        profile = query.first()
        if profile is None:
            raise ValueError(f"Profile #{profile_id} not found.")
        return profile

    def _resolved_query(self, preference: str, profile: Profile | None) -> str:
        if not profile:
            return preference or "Agent 开发 实习 校招"
        structured = profile.structured_profile_json or {}
        roles = list(profile.target_roles_json or structured.get("target_roles") or [])
        skills = list(structured.get("skills") or [])
        profile_terms = " ".join([*roles[:3], *skills[:10]]).strip()
        return " ".join(item for item in [preference, profile_terms] if item).strip() or "Agent 开发 实习 校招"

    def _external_query(self, profile: Profile | None) -> str:
        if profile:
            structured = profile.structured_profile_json or {}
            roles = list(profile.target_roles_json or structured.get("target_roles") or [])
            if roles:
                return str(roles[0])
            if profile.headline:
                return profile.headline
        return "Agent 开发实习生"

    def _profile_location(self, profile: Profile | None) -> str | None:
        if not profile:
            return None
        value = (profile.structured_profile_json or {}).get("location")
        return str(value).strip() if value else None

    def _input_mode(self, preference: str, profile: Profile | None) -> str:
        if preference and profile:
            return "preference_and_profile"
        if profile:
            return "profile_only"
        if preference:
            return "preference_only"
        return "browse"

    def _job_text(self, job: Job) -> str:
        structured = job.structured_jd_json or {}
        fields = [
            job.title,
            job.company,
            job.location,
            " ".join(structured.get("required_skills") or []),
            " ".join(structured.get("preferred_skills") or []),
            " ".join(structured.get("responsibilities") or []),
            " ".join(structured.get("qualifications") or []),
            job.raw_jd_text[:4000],
        ]
        return "\n".join(str(item) for item in fields if item)

    def _clamp(self, value: float) -> float:
        return max(0.0, min(1.0, value))
