from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Job, JobSearchResult, JobSearchSession, Profile
from app.models.schemas import JobDiscoveryRequest
from app.services.job_relevance import is_internship_like_posting, score_job_posting
from app.services.job_search import JobSearchService
from app.services.job_search_intent import JobSearchIntentService
from app.services.job_visibility import user_visible_jobs
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
    calibrated_match_score: float | None = None
    match_signal_confidence: float | None = None
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
        intent_service: JobSearchIntentService | None = None,
    ) -> None:
        self.settings = get_settings()
        self.job_search = job_search or JobSearchService()
        self.matcher = matcher or MatcherService()
        self.vector_index = vector_index or SQLiteVectorIndex()
        self.reranker = reranker or RerankerService(
            score_weight=max(self.settings.reranker_score_weight, 0.55),
            anchor_top_n=0,
        )
        self.retrieval_quality = RetrievalQualityService(self.settings)
        self.intent_service = intent_service or JobSearchIntentService()

    async def discover(
        self,
        db: Session,
        payload: JobDiscoveryRequest,
        *,
        tenant_id: str | None = None,
    ) -> JobSearchSession:
        profile = self._load_profile(db, payload.profile_id, tenant_id=tenant_id)
        preference = (payload.preference_text or "").strip()
        intent = await self.intent_service.plan(
            db,
            preference=preference,
            profile=profile,
            explicit_location=payload.location,
        )
        resolved_query = intent.retrieval_query
        location = " / ".join(intent.locations) or None
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
                    query=resolved_query,
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
                query_variants=intent.query_variants,
                excluded_terms=intent.excluded_terms,
            )
            retrieval_quality["intent_plan"] = intent.as_dict()
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
        query_variants: list[str] | None = None,
        excluded_terms: list[str] | None = None,
    ) -> tuple[list[DiscoveryCandidate], dict[str, Any]]:
        rows_query = db.query(Job)
        if self.settings.rbac_enabled:
            rows_query = rows_query.filter(Job.tenant_id == tenant_id)
        jobs = user_visible_jobs(rows_query.order_by(Job.updated_at.desc()).limit(800).all())
        if internship_only:
            jobs = [job for job in jobs if is_internship_like_posting(job)]
        if location:
            location_terms = [item.strip().lower() for item in location.replace("、", "/").split("/") if item.strip()]
            jobs = [
                job
                for job in jobs
                if not location_terms
                or any(term in (job.location or "").lower() for term in location_terms)
                or ("远程" in location_terms and "远程" in (job.location or ""))
            ]
        if excluded_terms:
            excluded = [term.lower() for term in excluded_terms if term.strip()]
            jobs = [
                job for job in jobs
                if not any(term in self._job_text(job).lower() for term in excluded)
            ]
        if not jobs:
            quality = self.retrieval_quality.assess(query, [], min_evidence_chunks=1)
            return [], quality

        queries = list(dict.fromkeys(item.strip() for item in (query_variants or [query]) if item.strip())) or [query]
        all_scored_jobs: list[tuple[Job, Any]] = []
        for job in jobs:
            relevance_rows = [score_job_posting(job, item) for item in queries]
            all_scored_jobs.append((job, max(relevance_rows, key=lambda item: item.score)))
        all_scored_jobs.sort(
            key=lambda item: (item[1].score, item[0].updated_at),
            reverse=True,
        )
        candidate_pool_size = max(24, min(160, limit * 8))
        lexical_pool_size = max(12, min(candidate_pool_size // 2, limit * 3))
        lexical_candidates = all_scored_jobs[:lexical_pool_size]
        chunk_lists = [
            self.vector_index.query_job_corpus(
                db,
                item,
                job_ids={job.id for job in jobs},
                top_k=candidate_pool_size,
                rerank=False,
            )
            for item in queries
        ]
        semantic_by_job: dict[int, float] = {}
        semantic_query_hits: dict[int, set[int]] = {}
        for query_index, chunks in enumerate(chunk_lists):
            for chunk in chunks:
                job_id = int((chunk.metadata or {}).get("job_id") or 0)
                if job_id:
                    vector_score = float(
                        ((chunk.metadata or {}).get("retrieval") or {}).get("vector_score")
                        or 0.0
                    )
                    semantic_by_job[job_id] = max(semantic_by_job.get(job_id, 0.0), vector_score)
                    semantic_query_hits.setdefault(job_id, set()).add(query_index)

        semantic_job_ids = {
            int((chunk.metadata or {}).get("job_id") or 0)
            for chunks in chunk_lists
            for chunk in chunks
            if int((chunk.metadata or {}).get("job_id") or 0)
        }
        candidate_job_ids = semantic_job_ids | {job.id for job, _ in lexical_candidates}
        lightweight_candidates = [
            (job, relevance)
            for job, relevance in all_scored_jobs
            if job.id in candidate_job_ids
        ][:candidate_pool_size]
        semantic_values = [semantic_by_job.get(job.id, 0.0) for job, _ in lightweight_candidates]
        semantic_min = min(semantic_values, default=0.0)
        semantic_max = max(semantic_values, default=0.0)

        def normalized_semantic(job_id: int) -> float:
            raw = semantic_by_job.get(job_id, 0.0)
            if semantic_max - semantic_min < 1e-6:
                return 0.5 if semantic_values else 0.0
            return self._clamp((raw - semantic_min) / (semantic_max - semantic_min))

        raw_candidates: list[dict[str, Any]] = []
        for job, relevance in lightweight_candidates:
            # The relevance scorer can legitimately exceed 28 for a strong
            # multi-intent match. The previous denominator saturated several
            # materially different jobs at 1.0 and erased the lexical signal.
            rule_norm = self._clamp((relevance.score + 8.0) / 44.0)
            semantic = self._clamp(semantic_by_job.get(job.id, 0.0))
            semantic_norm = normalized_semantic(job.id)
            retrieval_score = round(semantic_norm * 0.72 + rule_norm * 0.28, 6)
            raw_candidates.append(
                {
                    "job": job,
                    "text": self._job_text(job),
                    "chunk_type": "job",
                    "score": retrieval_score,
                    "metadata": {
                        "rule_score": relevance.score,
                        "rule_score_normalized": rule_norm,
                        "semantic_score": semantic,
                        "semantic_score_normalized": semantic_norm,
                        "query_hit_indexes": sorted(semantic_query_hits.get(job.id, set())),
                        "reasons": relevance.reasons,
                        "retrieval": {
                            "vector_score": semantic,
                            "vector_score_normalized": semantic_norm,
                            "lexical_score": rule_norm,
                            "first_stage_score": retrieval_score,
                            "retrieval_route": "semantic_corpus_union_lexical",
                        },
                    },
                }
            )
        raw_candidates.sort(key=lambda item: item["score"], reverse=True)
        rerank_query = self._rerank_query(query, queries)
        reranked = self.reranker.rerank_dicts(
            rerank_query,
            raw_candidates[: max(30, limit * 3)],
            top_k=max(30, limit * 3),
        )
        top_score = max((float(item.get("score") or 0.0) for item in reranked), default=0.0)
        candidate_score_floor = max(0.25, top_score * 0.55)
        unfiltered_count = len(reranked)
        reranked = [
            item for item in reranked if float(item.get("score") or 0.0) >= candidate_score_floor
        ]
        quality = self.retrieval_quality.assess(query, reranked, min_evidence_chunks=1)
        quality["query_strategy"] = {
            "name": "metadata_filter_then_semantic_union_and_rerank",
            "candidate_pool_size": len(raw_candidates),
            "semantic_weight": 0.72,
            "lexical_weight": 0.28,
            "candidate_score_floor": round(candidate_score_floor, 6),
            "weak_candidate_count": unfiltered_count - len(reranked),
            "query_count": len(queries),
            "query_variants": queries,
            "rerank_query": rerank_query,
            "semantic_fusion": "max_vector_score_across_queries",
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
            candidate.match_signal_confidence = self._match_signal_confidence(candidate.job)
            candidate.calibrated_match_score = round(
                50.0 + (candidate.match_score - 50.0) * candidate.match_signal_confidence,
                2,
            )
            candidate.matched_skills = list(match.matched_skills_json or [])
            candidate.missing_skills = list(match.missing_skills_json or [])
            candidate.final_score = round(
                candidate.retrieval_score * 0.75 + candidate.calibrated_match_score * 0.25,
                2,
            )
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
                        "ranking": {
                            "retrieval_weight": 0.75,
                            "match_weight": 0.25,
                            "raw_match_score": candidate.match_score,
                            "calibrated_match_score": candidate.calibrated_match_score,
                            "match_signal_confidence": candidate.match_signal_confidence,
                        },
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

    def _match_signal_confidence(self, job: Job) -> float:
        structured = job.structured_jd_json or {}
        payload = job.source_payload_json or {}
        granularity = str(
            payload.get("granularity")
            or structured.get("source_granularity")
            or "job_detail"
        )
        if granularity != "job_detail":
            return 0.0
        requirements = [
            str(item).strip()
            for item in (structured.get("required_skills") or [])
            if str(item).strip()
        ]
        return round(min(len(requirements) / 4.0, 1.0), 2)

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

    @staticmethod
    def _rerank_query(primary: str, queries: list[str]) -> str:
        compact = [" ".join(item.split())[:180] for item in queries if item.strip()]
        return "\n".join(dict.fromkeys([" ".join(primary.split())[:180], *compact]))[:480]

    def _clamp(self, value: float) -> float:
        return max(0.0, min(1.0, value))
