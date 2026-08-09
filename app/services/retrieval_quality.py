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
    ) -> dict[str, Any]:
        rows = list(chunks)
        non_empty = [row for row in rows if self._value(row, "text")]
        query_tokens = set(tokenize(query))
        evidence_tokens: set[str] = set()
        chunk_types: set[str] = set()
        first_stage_scores: list[float] = []
        final_scores: list[float] = []
        degraded_routes: list[str] = []
        multi_query_hits = 0
        for row in non_empty:
            evidence_tokens.update(tokenize(str(self._value(row, "text") or "")))
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

        query_coverage = len(query_tokens & evidence_tokens) / max(len(query_tokens), 1)
        top_first_stage = max(first_stage_scores, default=0.0)
        top_final = max(final_scores, default=0.0)
        expected = expected_chunk_types or set()
        type_coverage = len(chunk_types & expected) / max(len(expected), 1) if expected else 1.0
        minimum_chunks = min_evidence_chunks or self.settings.rag_min_evidence_chunks
        enough_chunks = len(non_empty) >= minimum_chunks
        relevance_signal = (
            query_coverage >= self.settings.rag_min_query_coverage
            or top_first_stage >= self.settings.rag_min_first_stage_score
        )
        passed = enough_chunks and relevance_signal

        reasons: list[str] = []
        if not enough_chunks:
            reasons.append(
                f"evidence_count={len(non_empty)} below min={minimum_chunks}"
            )
        if not relevance_signal:
            reasons.append(
                "both query coverage and first-stage relevance are below configured thresholds"
            )
        if expected and type_coverage == 0:
            reasons.append("no expected semantic chunk type was retrieved")
        if degraded_routes:
            reasons.append("reranker used a degraded language/provider route")

        confidence = (
            min(len(non_empty) / max(minimum_chunks, 1), 1.0) * 0.30
            + min(query_coverage / max(self.settings.rag_min_query_coverage, 0.01), 1.0) * 0.35
            + min(max(top_first_stage, 0.0) / max(self.settings.rag_min_first_stage_score, 0.01), 1.0) * 0.25
            + type_coverage * 0.10
        )
        return {
            "version": "careeragent-retrieval-quality-v1",
            "passed": passed,
            "decision": "supported" if passed else "insufficient_evidence",
            "confidence": round(min(max(confidence, 0.0), 1.0), 4),
            "evidence_count": len(non_empty),
            "query_token_count": len(query_tokens),
            "query_coverage": round(query_coverage, 4),
            "top_first_stage_score": round(top_first_stage, 6),
            "top_final_score": round(top_final, 6),
            "chunk_types": sorted(chunk_types),
            "expected_chunk_types": sorted(expected),
            "expected_type_coverage": round(type_coverage, 4),
            "multi_query_hit_count": multi_query_hits,
            "degraded_routes": sorted(set(degraded_routes)),
            "thresholds": {
                "min_evidence_chunks": minimum_chunks,
                "min_query_coverage": self.settings.rag_min_query_coverage,
                "min_first_stage_score": self.settings.rag_min_first_stage_score,
            },
            "reasons": reasons,
            "downstream_policy": (
                "allow_grounded_generation"
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
