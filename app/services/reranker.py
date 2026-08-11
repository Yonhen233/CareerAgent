import os
import re
from dataclasses import replace
from typing import Any

from app.core.config import Settings, get_settings
from app.services.embedding_service import expand_query_text, tokenize


_RERANKER_MODEL_CACHE: dict[str, Any] = {}
_RERANKER_FAILURES: dict[str, str] = {}
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


class RerankerService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        enabled: bool | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        score_weight: float | None = None,
        promotion_gap: float | None = None,
        anchor_top_n: int | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.enabled = self.settings.reranker_enabled if enabled is None else enabled
        self.provider = (provider or self.settings.reranker_provider).strip().lower()
        self.model_name = model_name or self.settings.reranker_model_name
        self.score_weight = self.settings.reranker_score_weight if score_weight is None else score_weight
        self.promotion_gap = self.settings.reranker_promotion_gap if promotion_gap is None else promotion_gap
        self.anchor_top_n = self.settings.reranker_anchor_top_n if anchor_top_n is None else anchor_top_n

    def rerank_chunks(self, query: str, candidates: list[Any], *, top_k: int) -> list[Any]:
        if not self.enabled or len(candidates) <= 1:
            return candidates[:top_k]

        texts = [str(getattr(candidate, "text", "") or "") for candidate in candidates]
        chunk_types = [str(getattr(candidate, "chunk_type", "") or "") for candidate in candidates]
        raw_scores, info = self._score_pairs(query, texts, chunk_types)
        normalized = self._normalize_scores(raw_scores)
        reranked = []
        for candidate, raw_score, norm_score in zip(candidates, raw_scores, normalized, strict=False):
            base_score = self._clamp(float(getattr(candidate, "score", 0.0) or 0.0))
            rerank_weight = self._clamp(self.score_weight)
            final_score = round(base_score * (1 - rerank_weight) + norm_score * rerank_weight, 6)
            metadata = dict(getattr(candidate, "metadata", None) or {})
            metadata["rerank"] = {
                "first_stage_score": base_score,
                "rerank_score": round(float(raw_score), 6),
                "rerank_score_normalized": round(norm_score, 6),
                "rerank_weight": rerank_weight,
                "promotion_gap": self.promotion_gap,
                "anchor_top_n": self.anchor_top_n,
                "final_score": final_score,
                **info,
            }
            try:
                reranked.append(replace(candidate, score=final_score, metadata=metadata))
            except TypeError:
                candidate.score = final_score
                candidate.metadata = metadata
                reranked.append(candidate)

        if info.get("language_route") == "cjk_lexical":
            return self._anchored_sort(reranked)[:top_k]
        return self._anchored_sort(reranked)[:top_k]

    def rerank_dicts(self, query: str, candidates: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
        if not self.enabled or len(candidates) <= 1:
            return candidates[:top_k]

        texts = [str(candidate.get("text") or "") for candidate in candidates]
        chunk_types = [str(candidate.get("chunk_type") or "") for candidate in candidates]
        raw_scores, info = self._score_pairs(query, texts, chunk_types)
        return self._rerank_dicts_with_scores(candidates, raw_scores, info=info, top_k=top_k)

    def rerank_dict_groups(
        self,
        groups: list[tuple[str, list[dict[str, Any]], int]],
    ) -> list[list[dict[str, Any]]]:
        """Rerank multiple query groups with one CrossEncoder predict call."""
        if not groups:
            return []
        if not self.enabled:
            return [candidates[:top_k] for _, candidates, top_k in groups]

        if self.provider in {"cross_encoder", "cross-encoder", "sentence_transformers"}:
            if any(self._requires_cjk_heuristic(query) for query, _, _ in groups):
                return [
                    self.rerank_dicts(query, candidates, top_k=top_k)
                    for query, candidates, top_k in groups
                ]
            try:
                model = self._load_cross_encoder()
                pairs = [
                    (query, str(candidate.get("text") or ""))
                    for query, candidates, _ in groups
                    for candidate in candidates
                ]
                flat_scores = [
                    float(score)
                    for score in model.predict(
                        pairs,
                        batch_size=self.settings.reranker_batch_size,
                        show_progress_bar=False,
                    )
                ]
                info = {
                    "reranker_provider": "cross_encoder",
                    "reranker_model": self.model_name,
                    "batched_query_count": len(groups),
                    "batched_pair_count": len(pairs),
                }
                output: list[list[dict[str, Any]]] = []
                offset = 0
                for _, candidates, top_k in groups:
                    size = len(candidates)
                    output.append(
                        self._rerank_dicts_with_scores(
                            candidates,
                            flat_scores[offset : offset + size],
                            info=info,
                            top_k=top_k,
                        )
                    )
                    offset += size
                return output
            except Exception as exc:  # noqa: BLE001
                if self.settings.reranker_provider_fallback.lower() != "heuristic":
                    raise
                fallback_reason = f"{self.provider}:{self.model_name} unavailable: {exc}"
                return [
                    self._rerank_dicts_with_scores(
                        candidates,
                        *self._heuristic_scores(
                            query,
                            [str(candidate.get("text") or "") for candidate in candidates],
                            [str(candidate.get("chunk_type") or "") for candidate in candidates],
                            fallback_reason=fallback_reason,
                        ),
                        top_k=top_k,
                    )
                    for query, candidates, top_k in groups
                ]

        return [self.rerank_dicts(query, candidates, top_k=top_k) for query, candidates, top_k in groups]

    def _rerank_dicts_with_scores(
        self,
        candidates: list[dict[str, Any]],
        raw_scores: list[float],
        info: dict[str, Any],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        normalized = self._normalize_scores(raw_scores)
        reranked: list[dict[str, Any]] = []
        for candidate, raw_score, norm_score in zip(candidates, raw_scores, normalized, strict=False):
            base_score = self._clamp(float(candidate.get("score") or 0.0))
            rerank_weight = self._clamp(self.score_weight)
            final_score = round(base_score * (1 - rerank_weight) + norm_score * rerank_weight, 6)
            metadata = dict(candidate.get("metadata") or {})
            metadata["rerank"] = {
                "first_stage_score": base_score,
                "rerank_score": round(float(raw_score), 6),
                "rerank_score_normalized": round(norm_score, 6),
                "rerank_weight": rerank_weight,
                "promotion_gap": self.promotion_gap,
                "anchor_top_n": self.anchor_top_n,
                "final_score": final_score,
                **info,
            }
            item = dict(candidate)
            item["score"] = final_score
            item["metadata"] = metadata
            reranked.append(item)

        if info.get("language_route") == "cjk_lexical":
            return self._anchored_sort(reranked)[:top_k]
        return self._anchored_sort(reranked)[:top_k]

    def _score_pairs(
        self,
        query: str,
        texts: list[str],
        chunk_types: list[str],
    ) -> tuple[list[float], dict[str, Any]]:
        if self.provider in {"cross_encoder", "cross-encoder", "sentence_transformers"}:
            if self._requires_cjk_heuristic(query):
                scores, info = self._heuristic_scores(
                    query,
                    texts,
                    chunk_types,
                    fallback_reason=(
                        f"{self.model_name} is English-only; used CJK lexical reranking for this query."
                    ),
                )
                info["language_route"] = "cjk_lexical"
                return scores, info
            try:
                model = self._load_cross_encoder()
                scores = model.predict(
                    [(query, text) for text in texts],
                    batch_size=self.settings.reranker_batch_size,
                    show_progress_bar=False,
                )
                return [float(score) for score in scores], {
                    "reranker_provider": "cross_encoder",
                    "reranker_model": self.model_name,
                }
            except Exception as exc:  # noqa: BLE001
                if self.settings.reranker_provider_fallback.lower() != "heuristic":
                    raise
                return self._heuristic_scores(
                    query,
                    texts,
                    chunk_types,
                    fallback_reason=f"{self.provider}:{self.model_name} unavailable: {exc}",
                )

        if self.provider in {"heuristic", "lexical"}:
            return self._heuristic_scores(query, texts, chunk_types)

        if self.settings.reranker_provider_fallback.lower() == "heuristic":
            return self._heuristic_scores(
                query,
                texts,
                chunk_types,
                fallback_reason=f"Unsupported reranker provider: {self.provider}",
            )
        raise ValueError(f"Unsupported reranker provider: {self.provider}")

    def _load_cross_encoder(self) -> Any:
        cache_key = self.model_name
        if cache_key in _RERANKER_FAILURES:
            raise RuntimeError(_RERANKER_FAILURES[cache_key])
        if cache_key in _RERANKER_MODEL_CACHE:
            return _RERANKER_MODEL_CACHE[cache_key]
        try:
            self._ensure_local_model_cache_env()
            from sentence_transformers import CrossEncoder  # type: ignore

            self.settings.embedding_cache_path.mkdir(parents=True, exist_ok=True)
            model = CrossEncoder(self.model_name)
            _RERANKER_MODEL_CACHE[cache_key] = model
            return model
        except Exception as exc:  # noqa: BLE001
            _RERANKER_FAILURES[cache_key] = str(exc)
            raise

    def _requires_cjk_heuristic(self, query: str) -> bool:
        return bool(CJK_RE.search(query)) and "ms-marco" in self.model_name.lower()

    def _ensure_local_model_cache_env(self) -> None:
        self.settings.embedding_cache_path.mkdir(parents=True, exist_ok=True)
        hf_home = self.settings.embedding_cache_path / "huggingface"
        hf_home.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(hf_home))
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(self.settings.embedding_cache_path))
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    def _heuristic_scores(
        self,
        query: str,
        texts: list[str],
        chunk_types: list[str],
        *,
        fallback_reason: str | None = None,
    ) -> tuple[list[float], dict[str, Any]]:
        expanded_query = expand_query_text(query)
        query_tokens = set(tokenize(expanded_query))
        scores = []
        for text, chunk_type in zip(texts, chunk_types, strict=False):
            text_lower = text.lower()
            chunk_tokens = set(tokenize(text))
            overlap = len(query_tokens & chunk_tokens) / max(len(query_tokens), 1)
            phrase_hits = sum(1 for token in query_tokens if len(token) >= 3 and token in text_lower)
            phrase_score = min(phrase_hits / max(len(query_tokens), 1), 1.0)
            type_score = 0.10 if chunk_type in {"project", "experience", "skill", "required_skills"} else 0.0
            scores.append(overlap * 0.62 + phrase_score * 0.28 + type_score)

        info: dict[str, Any] = {
            "reranker_provider": "heuristic",
            "reranker_model": "lexical_overlap_type_boost",
        }
        if fallback_reason:
            info["fallback_reason"] = fallback_reason
        return scores, info

    def _normalize_scores(self, scores: list[float]) -> list[float]:
        if not scores:
            return []
        minimum = min(scores)
        maximum = max(scores)
        if maximum == minimum:
            return [0.5 for _ in scores]
        return [(score - minimum) / (maximum - minimum) for score in scores]

    def _clamp(self, value: float) -> float:
        return max(0.0, min(value, 1.0))

    def _sort_with_promotion_gap(self, items: list[Any]) -> list[Any]:
        if not items:
            return []
        groups: list[list[Any]] = []
        current_group: list[Any] = []
        group_best_base = self._first_stage_score(items[0])
        for item in items:
            base_score = self._first_stage_score(item)
            if current_group and group_best_base - base_score > self.promotion_gap:
                groups.append(sorted(current_group, key=self._score, reverse=True))
                current_group = []
                group_best_base = base_score
            current_group.append(item)
        if current_group:
            groups.append(sorted(current_group, key=self._score, reverse=True))
        return [item for group in groups for item in group]

    def _anchored_sort(self, items: list[Any]) -> list[Any]:
        anchor_top_n = max(self.anchor_top_n, 0)
        if anchor_top_n <= 0:
            return self._sort_with_promotion_gap(items)
        anchor = items[:anchor_top_n]
        tail = items[anchor_top_n:]
        return anchor + self._sort_with_promotion_gap(tail)

    def _score(self, item: Any) -> float:
        if isinstance(item, dict):
            return float(item.get("score") or 0.0)
        return float(getattr(item, "score", 0.0) or 0.0)

    def _first_stage_score(self, item: Any) -> float:
        if isinstance(item, dict):
            metadata = item.get("metadata") or {}
        else:
            metadata = getattr(item, "metadata", None) or {}
        rerank = metadata.get("rerank") or {}
        return float(rerank.get("first_stage_score") or 0.0)
