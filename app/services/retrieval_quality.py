from __future__ import annotations

from typing import Any, Iterable

from app.core.config import Settings, get_settings
from app.services.embedding_service import tokenize


class RetrievalQualityError(RuntimeError):
    """Raised when retrieved evidence is too weak to support a generative action."""

    def __init__(self, message: str, *, report: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.report = report or {}


class RetrievalQualityService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def assess(
        self,
        query: str,
        chunks: Iterable[Any],
        *,
        expected_chunk_types: set[str] | None = None,
        min_evidence_chunks: int | None = None,
        expected_query_count: int | None = None,
    ) -> dict[str, Any]:
        rows = list(chunks)
        non_empty = [row for row in rows if str(self._value(row, "text") or "").strip()]
        unique_rows: list[Any] = []
        seen_texts: set[str] = set()
        for row in non_empty:
            fingerprint = " ".join(str(self._value(row, "text") or "").lower().split())
            if fingerprint in seen_texts:
                continue
            seen_texts.add(fingerprint)
            unique_rows.append(row)
        query_tokens = set(tokenize(query))
        evidence_tokens: set[str] = set()
        chunk_types: set[str] = set()
        first_stage_scores: list[float] = []
        final_scores: list[float] = []
        degraded_routes: list[str] = []
        multi_query_hits = 0
        covered_query_indexes: set[int] = set()
        supporting_evidence_count = 0
        for row in unique_rows:
            row_tokens = set(tokenize(str(self._value(row, "text") or "")))
            evidence_tokens.update(row_tokens)
            chunk_type = str(self._value(row, "chunk_type") or "")
            if chunk_type:
                chunk_types.add(chunk_type)
            metadata = self._metadata(row)
            retrieval = metadata.get("retrieval") or {}
            rerank = metadata.get("rerank") or {}
            first_stage_scores.append(
                float(rerank.get("first_stage_score", retrieval.get("first_stage_score", 0.0)) or 0.0)
            )
            final_scores.append(float(self._value(row, "score") or rerank.get("final_score") or 0.0))
            fallback_reason = rerank.get("fallback_reason")
            if fallback_reason:
                degraded_routes.append(str(fallback_reason))
            multi_query = retrieval.get("multi_query") or {}
            multi_query_hits += int(multi_query.get("hit_count") or 0)
            covered_query_indexes.update(
                int(item)
                for item in (multi_query.get("query_indexes") or [])
                if isinstance(item, int) or str(item).isdigit()
            )
            lexical_score = float(retrieval.get("lexical_score") or 0.0)
            first_stage_score = first_stage_scores[-1]
            row_query_coverage = len(query_tokens & row_tokens) / max(len(query_tokens), 1)
            if (
                lexical_score >= self.settings.rag_min_query_coverage
                or row_query_coverage >= self.settings.rag_min_query_coverage
                or first_stage_score >= self.settings.rag_min_first_stage_score
            ):
                supporting_evidence_count += 1

        query_coverage = len(query_tokens & evidence_tokens) / max(len(query_tokens), 1)
        top_first_stage = max(first_stage_scores, default=0.0)
        top_final = max(final_scores, default=0.0)
        expected = expected_chunk_types or set()
        type_coverage = len(chunk_types & expected) / max(len(expected), 1) if expected else 1.0
        minimum_chunks = min_evidence_chunks or self.settings.rag_min_evidence_chunks
        enough_chunks = len(unique_rows) >= minimum_chunks
        enough_support = supporting_evidence_count >= minimum_chunks
        expected_type_ok = not expected or bool(chunk_types & expected)
        query_count = max(int(expected_query_count or 0), 0)
        multi_query_coverage = (
            len(covered_query_indexes) / query_count if query_count else 1.0
        )
        relevance_signal = (
            query_coverage >= self.settings.rag_min_query_coverage
            or top_first_stage >= self.settings.rag_min_first_stage_score
        )
        passed = enough_chunks and enough_support and relevance_signal and expected_type_ok

        reasons: list[str] = []
        if not enough_chunks:
            reasons.append(
                f"unique_evidence_count={len(unique_rows)} below min={minimum_chunks}"
            )
        if enough_chunks and not enough_support:
            reasons.append(
                f"supporting_evidence_count={supporting_evidence_count} below min={minimum_chunks}"
            )
        if not relevance_signal:
            reasons.append(
                "both query coverage and first-stage relevance are below configured thresholds"
            )
        if expected and type_coverage == 0:
            reasons.append("no expected semantic chunk type was retrieved")
        if degraded_routes:
            reasons.append("reranker used a degraded language/provider route")
        if len(unique_rows) < len(non_empty):
            reasons.append("duplicate evidence was removed before quality scoring")
        if query_count and multi_query_coverage < 0.5:
            reasons.append("less than half of the retrieval query variants produced evidence")

        confidence = (
            min(len(unique_rows) / max(minimum_chunks, 1), 1.0) * 0.20
            + min(supporting_evidence_count / max(minimum_chunks, 1), 1.0) * 0.10
            + min(query_coverage / max(self.settings.rag_min_query_coverage, 0.01), 1.0) * 0.35
            + min(max(top_first_stage, 0.0) / max(self.settings.rag_min_first_stage_score, 0.01), 1.0) * 0.25
            + type_coverage * 0.10
        )
        return {
            "version": "careeragent-retrieval-quality-v2",
            "passed": passed,
            "decision": "supported" if passed else "insufficient_evidence",
            "confidence": round(min(max(confidence, 0.0), 1.0), 4),
            "evidence_count": len(non_empty),
            "unique_evidence_count": len(unique_rows),
            "duplicate_evidence_count": len(non_empty) - len(unique_rows),
            "supporting_evidence_count": supporting_evidence_count,
            "query_token_count": len(query_tokens),
            "query_coverage": round(query_coverage, 4),
            "top_first_stage_score": round(top_first_stage, 6),
            "top_final_score": round(top_final, 6),
            "chunk_types": sorted(chunk_types),
            "expected_chunk_types": sorted(expected),
            "expected_type_coverage": round(type_coverage, 4),
            "multi_query_hit_count": multi_query_hits,
            "expected_query_count": query_count,
            "covered_query_indexes": sorted(covered_query_indexes),
            "multi_query_coverage": round(multi_query_coverage, 4),
            "degraded_routes": sorted(set(degraded_routes)),
            "thresholds": {
                "min_evidence_chunks": minimum_chunks,
                "min_query_coverage": self.settings.rag_min_query_coverage,
                "min_first_stage_score": self.settings.rag_min_first_stage_score,
            },
            "reasons": reasons,
            "downstream_policy": (
                "allow_grounded_generation_with_citations"
                if passed and not degraded_routes
                else "allow_grounded_generation_with_citations_and_review"
                if passed
                else "allow_gap_detection_but_block_evidence-dependent_generation"
            ),
        }

    @staticmethod
    def _value(row: Any, name: str) -> Any:
        if isinstance(row, dict):
            return row.get(name)
        return getattr(row, name, None)

    def _metadata(self, row: Any) -> dict[str, Any]:
        value = self._value(row, "metadata")
        return value if isinstance(value, dict) else {}


def retrieval_failure_message(report: dict[str, Any]) -> str:
    reasons = "; ".join(str(item) for item in report.get("reasons") or [])
    return (
        "RAG evidence gate rejected evidence-dependent generation: "
        f"confidence={report.get('confidence')}, reasons={reasons or 'unknown'}"
    )
