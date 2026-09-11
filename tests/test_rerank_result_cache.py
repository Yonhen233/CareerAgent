from __future__ import annotations

import json

from app.core.config import Settings
from app.services.rerank_result_cache import RerankResultCacheService
from app.services.reranker import RerankerService


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.locks: dict[str, str] = {}

    def get(self, key: str):
        return self.values.get(key) or self.locks.get(key)

    def set(self, key: str, value: str, nx: bool = False, ex=None, px=None):
        del ex, px
        target = self.locks if key.endswith(":lock") else self.values
        if nx and key in target:
            return False
        target[key] = value
        return True

    def delete(self, *keys: str):
        for key in keys:
            self.values.pop(key, None)
            self.locks.pop(key, None)

    def eval(self, _script: str, _numkeys: int, key: str, token: str):
        if self.locks.get(key) == token:
            del self.locks[key]
            return 1
        return 0


def make_cache(redis_client=None) -> RerankResultCacheService:
    settings = Settings(
        _env_file=None,
        app_env="test",
        redis_enabled=redis_client is not None,
        reranker_result_cache_enabled=True,
        reranker_cache_l1_max_entries=16,
        reranker_cache_l1_max_bytes=100_000,
    )
    return RerankResultCacheService(settings=settings, redis_client=redis_client)


def context(cache: RerankResultCacheService, candidates: list[dict]):
    return cache.build_context(
        "Agent RAG",
        candidates,
        provider="cross_encoder",
        model_name="test-reranker",
        language_route="cross_encoder",
        algorithm_version="test-v1",
        postprocess={"score_weight": 0.3, "promotion_gap": 0.02, "anchor_top_n": 5},
    )


def test_pair_cache_reuses_score_when_request_candidate_set_changes():
    cache = make_cache()
    first = context(
        cache,
        [
            {"uid": "a", "text": "Agent RAG workflow", "chunk_type": "project", "score": 0.8},
            {"uid": "b", "text": "FastAPI service", "chunk_type": "project", "score": 0.7},
        ],
    )
    calls: list[list[str]] = []

    def compute(items):
        calls.append([item.candidate_id for item in items])
        return [0.9, 0.4]

    scores, info = cache.get_or_compute_pair_scores(first, compute)
    assert scores == [0.9, 0.4]
    assert info["pair_hits"] == 0
    assert info["pair_misses"] == 2
    assert info["pair_computed"] == 2
    second = context(
        cache,
        [{"uid": "a", "text": "Agent RAG workflow", "chunk_type": "project", "score": 0.1}],
    )
    scores, info = cache.get_or_compute_pair_scores(second, lambda items: [0.0 for _ in items])
    assert scores == [0.9]
    assert calls == [["a", "b"]]
    assert info["pair_misses"] == 0


def test_request_cache_contains_only_raw_scores_and_is_strictly_validated():
    redis = FakeRedis()
    cache = make_cache(redis)
    ctx = context(cache, [{"uid": "a", "text": "Agent RAG workflow", "chunk_type": "project", "score": 0.8}])
    cache.put_request(ctx, [0.73])
    value = next(iter(redis.values.values()))
    assert "Agent RAG workflow" not in value
    parsed = json.loads(value)
    assert parsed["scores"][0]["raw_score"] == 0.73
    scores, layer = cache.get_request(ctx)
    assert scores == [0.73]
    assert layer == "l1"  # L1 is populated on write and avoids a Redis round trip.
    altered = cache.build_context(
        "Agent RAG",
        [{"uid": "a", "text": "Agent RAG workflow changed", "chunk_type": "project", "score": 0.8}],
        provider="cross_encoder",
        model_name="test-reranker",
        language_route="cross_encoder",
        algorithm_version="test-v1",
        postprocess={"score_weight": 0.3, "promotion_gap": 0.02, "anchor_top_n": 5},
    )
    assert cache.get_request(altered)[0] is None


def test_duplicate_content_is_computed_once_and_mapped_to_each_candidate():
    cache = make_cache()
    ctx = context(
        cache,
        [
            {"uid": "a", "text": "same evidence", "chunk_type": "project", "score": 0.5},
            {"uid": "b", "text": "same evidence", "chunk_type": "project", "score": 0.4},
        ],
    )
    calls = 0

    def compute(items):
        nonlocal calls
        calls += 1
        assert len(items) == 1
        return [0.61]

    scores, _ = cache.get_or_compute_pair_scores(ctx, compute)
    assert calls == 1
    assert scores == [0.61, 0.61]


def test_reranker_request_cache_skips_second_model_call():
    class FakeModel:
        calls = 0

        def predict(self, pairs, *, batch_size, show_progress_bar):
            del batch_size, show_progress_bar
            self.calls += 1
            return [0.8 if "Agent" in text else 0.2 for _, text in pairs]

    service = RerankerService(enabled=True, provider="cross_encoder", anchor_top_n=0)
    model = FakeModel()
    service._load_cross_encoder = lambda: model
    candidates = [
        {"uid": "a", "text": "Agent workflow", "chunk_type": "project", "score": 0.5},
        {"uid": "b", "text": "ordinary service", "chunk_type": "project", "score": 0.5},
    ]
    first = service.rerank_dicts("Agent", candidates, top_k=2)
    second = service.rerank_dicts("Agent", candidates, top_k=2)
    assert model.calls == 1
    assert first[0]["uid"] == second[0]["uid"] == "a"
    assert second[0]["metadata"]["rerank"]["reranker_cache"]["request_hit"] is True
