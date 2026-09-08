import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.services.embedding_service import EmbeddingService, cosine_similarity, expand_query_text, tokenize
from app.services.rerank_result_cache import CacheCandidate, RerankCacheContext, RerankResultCacheService


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
        self.result_cache = RerankResultCacheService(settings=self.settings)

    def rerank_chunks(self, query: str, candidates: list[Any], *, top_k: int) -> list[Any]:
        if not self.enabled or len(candidates) <= 1:
            return candidates[:top_k]

        payloads = [self._chunk_payload(candidate) for candidate in candidates]
        raw_scores, info = self._score_payloads(query, payloads)
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

        payloads = [self._dict_payload(candidate) for candidate in candidates]
        raw_scores, info = self._score_payloads(query, payloads)
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
                contexts: list[tuple[str, list[dict[str, Any]], int, RerankCacheContext]] = []
                query_by_hash: dict[str, str] = {}
                for query, candidates, top_k in groups:
                    payloads = [self._dict_payload(candidate) for candidate in candidates]
                    context = self._build_cache_context(query, payloads)
                    contexts.append((query, candidates, top_k, context))
                    query_by_hash[context.query_hash] = query
                all_items: dict[str, CacheCandidate] = {}
                request_scores: dict[int, list[float]] = {}
                for group_index, (_, _, _, context) in enumerate(contexts):
                    cached_scores, _ = self.result_cache.get_request(context)
                    if cached_scores is not None:
                        request_scores[group_index] = cached_scores
                    for item in context.candidates:
                        all_items.setdefault(item.pair_key, item)
                missing_groups = [index for index in range(len(contexts)) if index not in request_scores]
                merged_candidates = tuple(all_items.values())
                if missing_groups:
                    missing_pair_items = tuple(
                        item
                        for item in merged_candidates
                        if any(
                            item.pair_key == candidate.pair_key
                            for group_index in missing_groups
                            for candidate in contexts[group_index][3].candidates
                        )
                    )
                    compute_context = RerankCacheContext(
                        request_key="group-compute",
                        query_hash="group-compute",
                        provider=contexts[0][3].provider,
                        model_fingerprint=contexts[0][3].model_fingerprint,
                        language_route=contexts[0][3].language_route,
                        algorithm_version=contexts[0][3].algorithm_version,
                        candidates=missing_pair_items,
                    )
                    def compute(items: list[CacheCandidate]) -> list[float]:
                        model = self._load_cross_encoder()
                        return [
                            float(score)
                            for score in model.predict(
                                [(query_by_hash[item.query_hash], item.text) for item in items],
                                batch_size=self.settings.reranker_batch_size,
                                show_progress_bar=False,
                            )
                        ]

                    computed_scores, pair_info = self.result_cache.get_or_compute_pair_scores(compute_context, compute)
                    score_by_pair = {
                        item.pair_key: score for item, score in zip(compute_context.candidates, computed_scores, strict=False)
                    }
                    for group_index in missing_groups:
                        context = contexts[group_index][3]
                        scores = [score_by_pair[item.pair_key] for item in context.candidates]
                        request_scores[group_index] = scores
                        self.result_cache.put_request(context, scores)
                else:
                    pair_info = {"enabled": True, "pair_hits": 0, "pair_misses": 0, "cache_layer": "request"}
                info = {
                    "reranker_provider": "cross_encoder",
                    "reranker_model": self.model_name,
                    "batched_query_count": len(groups),
                    "batched_pair_count": sum(len(candidates) for _, candidates, _ in groups),
                    "reranker_cache": pair_info,
                }
                output: list[list[dict[str, Any]]] = []
                for group_index, (_, candidates, top_k, _) in enumerate(contexts):
                    output.append(
                        self._rerank_dicts_with_scores(
                            candidates,
                            request_scores[group_index],
                            info=info,
                            top_k=top_k,
                        )
                    )
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

    def _score_payloads(
        self,
        query: str,
        payloads: list[dict[str, Any]],
    ) -> tuple[list[float], dict[str, Any]]:
        """Score candidates through request/pair caches, then rebuild ranking outside the cache."""

        chunk_types = [str(payload.get("chunk_type") or "") for payload in payloads]
        route = self._cache_route(query)
        if route is None:
            return self._score_pairs(
                query,
                [str(payload.get("text") or "") for payload in payloads],
                chunk_types,
            )
        provider, model_name, language_route = route
        context = self._build_cache_context(query, payloads)
        request_scores, request_layer = self.result_cache.get_request(context)
        if request_scores is not None:
            return request_scores, {
                "reranker_provider": provider,
                "reranker_model": model_name,
                "language_route": language_route,
                "reranker_cache": {
                    "enabled": True,
                    "request_hit": True,
                    "request_layer": request_layer,
                    "pair_hits": 0,
                    "pair_misses": 0,
                },
            }

        computed_info: dict[str, Any] = {}

        def compute(items: list[CacheCandidate]) -> list[float]:
            scores, info = self._score_pairs(
                query,
                [item.text for item in items],
                [item.chunk_type for item in items],
            )
            computed_info.update(info)
            if len(scores) != len(items) and len(scores) == len(context.candidates):
                scores_by_pair: dict[str, float] = {}
                for candidate, score in zip(context.candidates, scores, strict=False):
                    scores_by_pair.setdefault(candidate.pair_key, float(score))
                return [scores_by_pair[item.pair_key] for item in items]
            return scores

        raw_scores, pair_info = self.result_cache.get_or_compute_pair_scores(context, compute)
        self.result_cache.put_request(context, raw_scores)
        info = {
            "reranker_provider": computed_info.get("reranker_provider", provider),
            "reranker_model": computed_info.get("reranker_model", model_name),
            "language_route": computed_info.get("language_route", language_route),
            "reranker_cache": {
                **pair_info,
                "request_hit": False,
            },
        }
        for key in ("fallback_reason",):
            if key in computed_info:
                info[key] = computed_info[key]
        return raw_scores, info

    def _build_cache_context(
        self,
        query: str,
        payloads: list[dict[str, Any]],
    ) -> RerankCacheContext:
        provider, model_name, language_route = self._cache_route(query) or (
            self.provider,
            self.model_name,
            "default",
        )
        return self.result_cache.build_context(
            query,
            payloads,
            provider=provider,
            model_name=model_name,
            language_route=language_route,
            algorithm_version=getattr(self.settings, "reranker_cache_algorithm_version", "raw-score-v2"),
            postprocess={
                "score_weight": self.score_weight,
                "promotion_gap": self.promotion_gap,
                "anchor_top_n": self.anchor_top_n,
            },
        )

    def _cache_route(self, query: str) -> tuple[str, str, str] | None:
        if not self.enabled or not self.result_cache.enabled:
            return None
        if self.provider in {"heuristic", "lexical"}:
            # Heuristic output is a formal fallback, never a production cache value.
            return None
        if self.provider in {"cross_encoder", "cross-encoder", "sentence_transformers"}:
            if self._requires_cjk_heuristic(query):
                return (
                    "multilingual_embedding",
                    self.settings.embedding_model_name,
                    "cjk_semantic",
                )
            return ("cross_encoder", self.model_name, "cross_encoder")
        return None

    def _dict_payload(self, candidate: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(candidate.get("metadata") or {})
        return {
            **candidate,
            "text": self._candidate_text(candidate),
            "chunk_type": str(candidate.get("chunk_type") or ""),
            "source_type": str(candidate.get("source_type") or metadata.get("source_type") or metadata.get("source") or ""),
            "metadata": metadata,
        }

    def _chunk_payload(self, candidate: Any) -> dict[str, Any]:
        metadata = dict(getattr(candidate, "metadata", None) or {})
        return {
            "candidate_id": str(
                getattr(candidate, "uid", None)
                or getattr(candidate, "id", None)
                or getattr(candidate, "chunk_uid", None)
                or ""
            ),
            "text": self._candidate_text(candidate),
            "chunk_type": str(getattr(candidate, "chunk_type", "") or ""),
            "source_type": str(metadata.get("source_type") or metadata.get("source") or ""),
            "score": float(getattr(candidate, "score", 0.0) or 0.0),
            "metadata": metadata,
        }

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

    @staticmethod
    def _candidate_text(candidate: Any) -> str:
        if isinstance(candidate, dict):
            text = str(candidate.get("text") or "")
            metadata = dict(candidate.get("metadata") or {})
        else:
            text = str(getattr(candidate, "text", "") or "")
            metadata = dict(getattr(candidate, "metadata", None) or {})
        context = str(metadata.get("retrieval_context") or "").strip()
        if not context or context in text:
            return text
        return f"[简历上下文] {context}\n[当前证据] {text}"

    def _score_pairs(
        self,
        query: str,
        texts: list[str],
        chunk_types: list[str],
    ) -> tuple[list[float], dict[str, Any]]:
        if self.provider in {"cross_encoder", "cross-encoder", "sentence_transformers"}:
            if self._requires_cjk_heuristic(query):
                return self._multilingual_embedding_scores(query, texts)
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
            local_model = self._resolve_local_model_path()
            model = CrossEncoder(str(local_model) if local_model is not None else self.model_name)
            _RERANKER_MODEL_CACHE[cache_key] = model
            return model
        except Exception as exc:  # noqa: BLE001
            _RERANKER_FAILURES[cache_key] = str(exc)
            raise

    def _resolve_local_model_path(self) -> Path | None:
        configured = Path(self.model_name).expanduser()
        if configured.is_dir() and (configured / "config.json").exists():
            return configured

        model_dir = self.settings.embedding_cache_path / (
            "models--" + self.model_name.replace("/", "--")
        )
        candidates = [
            path
            for path in (model_dir / "snapshots").glob("*/")
            if (path / "config.json").exists() and (path / "model.safetensors").exists()
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def _requires_cjk_heuristic(self, query: str) -> bool:
        return bool(CJK_RE.search(query)) and "ms-marco" in self.model_name.lower()

    def _multilingual_embedding_scores(
        self,
        query: str,
        texts: list[str],
    ) -> tuple[list[float], dict[str, Any]]:
        embedding = EmbeddingService(settings=self.settings)
        batch = embedding.embed_texts([query, *texts])
        query_vector = batch.vectors[0]
        scores = [cosine_similarity(query_vector, vector) for vector in batch.vectors[1:]]
        info = {
            "reranker_provider": "multilingual_embedding",
            "reranker_model": batch.model,
            "language_route": "cjk_semantic",
        }
        if batch.fallback_reason:
            info["fallback_reason"] = batch.fallback_reason
        return scores, info

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
