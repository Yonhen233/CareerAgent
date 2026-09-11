"""Production cache for expensive reranker results.

The cache deliberately stores raw model scores, never rendered text or final
ordering.  Final ranking is rebuilt for every request so changes to the
candidate set, first-stage scores, anchors, and promotion policy take effect
immediately.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from app.core.config import Settings, get_settings
from app.core.redis_client import (
    RedisUnavailableError,
    get_redis_client,
    is_redis_transport_error,
    mark_redis_unavailable,
    redis_key,
)


_CACHE_SCHEMA_VERSION = 1
_WHITESPACE_RE = re.compile(r"\s+")
_METRICS_LOCK = threading.Lock()
_GLOBAL_METRICS: dict[str, int] = {
    "request_hits": 0,
    "request_misses": 0,
    "pair_hits": 0,
    "pair_misses": 0,
    "redis_hits": 0,
    "redis_misses": 0,
    "redis_errors": 0,
    "lock_waits": 0,
    "lock_failures": 0,
    "writes": 0,
    "invalid_entries": 0,
}


def _metric(name: str, amount: int = 1) -> None:
    with _METRICS_LOCK:
        _GLOBAL_METRICS[name] = _GLOBAL_METRICS.get(name, 0) + amount


def cache_metrics_snapshot() -> dict[str, int]:
    with _METRICS_LOCK:
        return dict(_GLOBAL_METRICS)


def _sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_query(value: str) -> str:
    """Normalize only representation noise; do not add synonyms or rewrite intent."""

    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", str(value or ""))).strip()


@dataclass(frozen=True)
class CacheCandidate:
    index: int
    query_hash: str
    candidate_id: str
    text: str
    chunk_type: str
    source_type: str
    retrieval_context: str
    scope_hash: str
    content_hash: str
    pair_key: str
    match_token: str


@dataclass(frozen=True)
class RerankCacheContext:
    request_key: str
    query_hash: str
    provider: str
    model_fingerprint: str
    language_route: str
    algorithm_version: str
    candidates: tuple[CacheCandidate, ...]


class CacheCoordinationError(RuntimeError):
    """Raised when a distributed single-flight lock cannot be acquired safely."""


class _L1Cache:
    def __init__(self, *, max_entries: int, max_bytes: int, ttl_seconds: int) -> None:
        self.max_entries = max(1, int(max_entries))
        self.max_bytes = max(1024, int(max_bytes))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._items: OrderedDict[str, tuple[float, int, dict[str, Any]]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, size, value = item
            if expires_at <= now:
                self._items.pop(key, None)
                self._bytes -= size
                return None
            self._items.move_to_end(key)
            return dict(value)

    def put(self, key: str, value: dict[str, Any]) -> None:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        size = len(serialized.encode("utf-8"))
        if size > self.max_bytes:
            return
        with self._lock:
            old = self._items.pop(key, None)
            if old:
                self._bytes -= old[1]
            self._items[key] = (time.monotonic() + self.ttl_seconds, size, dict(value))
            self._bytes += size
            while len(self._items) > self.max_entries or self._bytes > self.max_bytes:
                _, (_, evicted_size, _) = self._items.popitem(last=False)
                self._bytes -= evicted_size


class RerankResultCacheService:
    """L1 bounded cache plus Redis L2 and distributed single-flight."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        redis_client: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.enabled = bool(getattr(self.settings, "reranker_result_cache_enabled", True))
        self.redis_client = redis_client
        self._redis_next_retry_at = 0.0
        self._local_locks: dict[str, tuple[threading.Lock, int]] = {}
        self._local_locks_guard = threading.Lock()
        self._l1 = _L1Cache(
            max_entries=getattr(self.settings, "reranker_cache_l1_max_entries", 256),
            max_bytes=getattr(self.settings, "reranker_cache_l1_max_bytes", 8_000_000),
            ttl_seconds=getattr(self.settings, "reranker_cache_l1_ttl_seconds", 300),
        )

    def build_context(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        provider: str,
        model_name: str,
        language_route: str,
        algorithm_version: str,
        postprocess: dict[str, Any],
    ) -> RerankCacheContext:
        normalized_query = normalize_query(query)
        query_hash = _sha256({"query": normalized_query})
        model_fingerprint = _sha256(
            {
                "provider": provider,
                "model": model_name,
                "model_revision": getattr(self.settings, "reranker_model_revision", "configured"),
                "tokenizer_revision": getattr(self.settings, "reranker_tokenizer_revision", "configured"),
                "max_sequence_length": getattr(self.settings, "reranker_max_sequence_length", 512),
                "truncation_policy": getattr(self.settings, "reranker_truncation_policy", "library_default"),
                "algorithm_version": algorithm_version,
            }
        )
        built: list[CacheCandidate] = []
        for index, candidate in enumerate(candidates):
            metadata = dict(candidate.get("metadata") or {})
            text = str(candidate.get("text") or "")
            chunk_type = str(candidate.get("chunk_type") or metadata.get("chunk_type") or "")
            source_type = str(
                candidate.get("source_type")
                or metadata.get("source_type")
                or metadata.get("source")
                or chunk_type
                or "unknown"
            )
            retrieval_context = normalize_query(str(metadata.get("retrieval_context") or ""))
            scope_hash = _sha256(self._scope_payload(metadata, source_type))
            content_hash = _sha256(
                {
                    "text": text,
                    "retrieval_context": retrieval_context,
                    "chunk_type": chunk_type,
                    "source_type": source_type,
                    "language_route": language_route,
                }
            )
            candidate_id = self._candidate_id(candidate, index)
            pair_key = self._key(
                "pair",
                scope_hash,
                provider,
                model_fingerprint,
                language_route,
                algorithm_version,
                query_hash,
                content_hash,
            )
            match_token = _sha256(
                {
                    "candidate_id": candidate_id,
                    "content_hash": content_hash,
                    "scope_hash": scope_hash,
                    "query_hash": query_hash,
                    "first_stage_score": round(float(candidate.get("score") or 0.0), 8),
                }
            )
            built.append(
                CacheCandidate(
                    index=index,
                    query_hash=query_hash,
                    candidate_id=candidate_id,
                    text=text,
                    chunk_type=chunk_type,
                    source_type=source_type,
                    retrieval_context=retrieval_context,
                    scope_hash=scope_hash,
                    content_hash=content_hash,
                    pair_key=pair_key,
                    match_token=match_token,
                )
            )
        candidate_fingerprint = _sha256(
            sorted(
                (
                    item.candidate_id,
                    item.content_hash,
                    item.scope_hash,
                    item.match_token,
                )
                for item in built
            )
        )
        request_key = self._key(
            "request",
            provider,
            model_fingerprint,
            language_route,
            algorithm_version,
            query_hash,
            candidate_fingerprint,
            _sha256(postprocess),
        )
        return RerankCacheContext(
            request_key=request_key,
            query_hash=query_hash,
            provider=provider,
            model_fingerprint=model_fingerprint,
            language_route=language_route,
            algorithm_version=algorithm_version,
            candidates=tuple(built),
        )

    def get_request(self, context: RerankCacheContext) -> tuple[list[float] | None, str | None]:
        if not self.enabled:
            return None, None
        value, layer = self._get_json(context.request_key)
        if not self._valid_request(value, context):
            if value is not None:
                _metric("invalid_entries")
            _metric("request_misses")
            return None, None
        scores_by_token = {str(item["token"]): float(item["raw_score"]) for item in value["scores"]}
        if set(scores_by_token) != {item.match_token for item in context.candidates}:
            _metric("invalid_entries")
            _metric("request_misses")
            return None, None
        _metric("request_hits")
        return [scores_by_token[item.match_token] for item in context.candidates], layer

    def put_request(self, context: RerankCacheContext, scores: list[float]) -> None:
        if not self.enabled or not self._valid_scores(scores, len(context.candidates)):
            return
        value = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "key": context.request_key,
            "query_hash": context.query_hash,
            "provider": context.provider,
            "model_fingerprint": context.model_fingerprint,
            "language_route": context.language_route,
            "algorithm_version": context.algorithm_version,
            "scores": [
                {"token": item.match_token, "raw_score": float(score)}
                for item, score in zip(context.candidates, scores, strict=False)
            ],
        }
        self._put_json(context.request_key, value)

    def get_or_compute_pair_scores(
        self,
        context: RerankCacheContext,
        compute: Callable[[list[CacheCandidate]], list[float]],
    ) -> tuple[list[float], dict[str, Any]]:
        """Read pairs, then compute only misses under local/distributed locks."""

        if not self.enabled:
            scores = compute(list(context.candidates))
            return scores, {"enabled": False, "pair_hits": 0, "pair_misses": len(context.candidates)}

        results: dict[str, float] = {}
        pair_scores_by_key: dict[str, float] = {}
        for item in context.candidates:
            if item.pair_key in pair_scores_by_key:
                continue
            value, _ = self._get_json(item.pair_key)
            score = self._valid_pair(value, item, context)
            if score is None:
                continue
            pair_scores_by_key[item.pair_key] = score
        for item in context.candidates:
            if item.pair_key in pair_scores_by_key:
                results[item.match_token] = pair_scores_by_key[item.pair_key]
        hit_count = len(pair_scores_by_key)
        misses_by_key: dict[str, CacheCandidate] = {}
        for item in context.candidates:
            if item.match_token not in results:
                misses_by_key.setdefault(item.pair_key, item)
        misses = list(misses_by_key.values())
        _metric("pair_hits", hit_count)
        _metric("pair_misses", len(misses))
        if not misses:
            return [results[item.match_token] for item in context.candidates], {
                "enabled": True,
                "pair_hits": hit_count,
                "pair_misses": 0,
                "cache_layer": "pair",
            }

        owned: list[CacheCandidate] = []
        locks: list[tuple[str, threading.Lock, str | None]] = []
        try:
            for item in sorted(misses, key=lambda value: value.pair_key):
                local_lock = self._get_local_lock(item.pair_key)
                local_lock.acquire()
                locks.append((item.pair_key, local_lock, None))
                value, _ = self._get_json(item.pair_key)
                score = self._valid_pair(value, item, context)
                if score is not None:
                    results[item.match_token] = score
                    continue
                token = self._acquire_distributed_lock(item.pair_key)
                if token is None:
                    value, _ = self._wait_for_pair(item, context)
                    score = self._valid_pair(value, item, context)
                    if score is not None:
                        results[item.match_token] = score
                        continue
                    _metric("lock_failures")
                    raise CacheCoordinationError(f"Unable to acquire reranker cache lock: {item.pair_key}")
                locks[-1] = (item.pair_key, local_lock, token)
                owned.append(item)

            if owned:
                # Lock acquisition is sorted by key to avoid deadlocks, but the
                # model callback must receive the same order as the caller.
                owned_keys = {item.pair_key for item in owned}
                ordered_owned: list[CacheCandidate] = []
                seen_owned: set[str] = set()
                for item in context.candidates:
                    if item.pair_key in owned_keys and item.pair_key not in seen_owned:
                        ordered_owned.append(item)
                        seen_owned.add(item.pair_key)
                owned = ordered_owned
                computed = compute(owned)
                if not self._valid_scores(computed, len(owned)):
                    raise ValueError("Reranker returned an invalid score vector.")
                for item, score in zip(owned, computed, strict=False):
                    results[item.match_token] = float(score)
                    self._put_pair(item, context, float(score))
        finally:
            for key, local_lock, token in reversed(locks):
                if token:
                    self._release_distributed_lock(key, token)
                local_lock.release()
                self._release_local_lock(key, local_lock)

        pair_scores = {
            item.pair_key: results[item.match_token]
            for item in context.candidates
            if item.match_token in results
        }
        for item in context.candidates:
            if item.match_token not in results and item.pair_key in pair_scores:
                results[item.match_token] = pair_scores[item.pair_key]
        if len(results) != len(context.candidates):
            raise CacheCoordinationError("Reranker cache single-flight completed without all pair scores.")
        return [results[item.match_token] for item in context.candidates], {
            "enabled": True,
            # ``misses`` is the set that missed the first cache read. Some of
            # those entries may become hits while waiting for another worker's
            # single-flight computation. Count those recovered entries as
            # hits, but never subtract the number of computed entries twice.
            "pair_hits": hit_count + len(misses) - len(owned),
            "pair_misses": len(misses),
            "pair_computed": len(owned),
            "cache_layer": "pair_or_compute",
        }

    def _put_pair(self, item: CacheCandidate, context: RerankCacheContext, score: float) -> None:
        value = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "key": item.pair_key,
            "query_hash": item.query_hash,
            "content_hash": item.content_hash,
            "scope_hash": item.scope_hash,
            "provider": context.provider,
            "model_fingerprint": context.model_fingerprint,
            "language_route": context.language_route,
            "algorithm_version": context.algorithm_version,
            "raw_score": score,
        }
        self._put_json(item.pair_key, value)

    def _valid_pair(self, value: dict[str, Any] | None, item: CacheCandidate, context: RerankCacheContext) -> float | None:
        if not isinstance(value, dict):
            return None
        if (
            value.get("schema_version") != _CACHE_SCHEMA_VERSION
            or value.get("key") != item.pair_key
            or value.get("query_hash") != item.query_hash
            or value.get("content_hash") != item.content_hash
            or value.get("scope_hash") != item.scope_hash
            or value.get("provider") != context.provider
            or value.get("model_fingerprint") != context.model_fingerprint
            or value.get("language_route") != context.language_route
            or value.get("algorithm_version") != context.algorithm_version
        ):
            return None
        try:
            score = float(value["raw_score"])
        except (KeyError, TypeError, ValueError):
            return None
        return score if math.isfinite(score) else None

    def _valid_request(self, value: dict[str, Any] | None, context: RerankCacheContext) -> bool:
        if not isinstance(value, dict):
            return False
        return (
            value.get("schema_version") == _CACHE_SCHEMA_VERSION
            and value.get("key") == context.request_key
            and value.get("query_hash") == context.query_hash
            and value.get("provider") == context.provider
            and value.get("model_fingerprint") == context.model_fingerprint
            and value.get("language_route") == context.language_route
            and value.get("algorithm_version") == context.algorithm_version
            and self._valid_scores(
                [item.get("raw_score") for item in value.get("scores", []) if isinstance(item, dict)],
                len(context.candidates),
            )
        )

    @staticmethod
    def _valid_scores(scores: list[Any], expected: int) -> bool:
        if len(scores) != expected:
            return False
        try:
            return all(math.isfinite(float(score)) for score in scores)
        except (TypeError, ValueError):
            return False

    def _get_json(self, key: str) -> tuple[dict[str, Any] | None, str | None]:
        local = self._l1.get(key)
        if local is not None:
            return local, "l1"
        client = self._get_redis()
        if client is None:
            return None, None
        try:
            payload = client.get(key)
            if not payload:
                _metric("redis_misses")
                return None, None
            value = json.loads(payload)
            if not isinstance(value, dict):
                return None, None
            self._l1.put(key, value)
            _metric("redis_hits")
            return value, "redis"
        except Exception as exc:  # noqa: BLE001
            _metric("redis_errors")
            if is_redis_transport_error(exc):
                mark_redis_unavailable(exc)
            return None, None

    def _put_json(self, key: str, value: dict[str, Any]) -> None:
        self._l1.put(key, value)
        _metric("writes")
        client = self._get_redis()
        if client is None:
            return
        try:
            client.set(
                key,
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                ex=max(1, int(getattr(self.settings, "reranker_cache_ttl_seconds", 86400))),
            )
        except Exception as exc:  # noqa: BLE001
            _metric("redis_errors")
            if is_redis_transport_error(exc):
                mark_redis_unavailable(exc)

    def _get_redis(self) -> Any | None:
        if self.redis_client is not None:
            return self.redis_client
        now = time.monotonic()
        if now < self._redis_next_retry_at:
            return None
        if not bool(getattr(self.settings, "redis_enabled", False)):
            self._redis_next_retry_at = now + float(
                getattr(self.settings, "reranker_cache_redis_retry_seconds", 5.0)
            )
            return None
        self._redis_next_retry_at = now + float(
            getattr(self.settings, "reranker_cache_redis_retry_seconds", 5.0)
        )
        try:
            self.redis_client = get_redis_client(
                socket_timeout_seconds=float(
                    getattr(self.settings, "reranker_cache_redis_timeout_seconds", 0.75)
                )
            )
            return self.redis_client
        except RedisUnavailableError:
            _metric("redis_errors")
            return None

    def _acquire_distributed_lock(self, pair_key: str) -> str | None:
        client = self._get_redis()
        if client is None:
            return "local-only"
        token = uuid.uuid4().hex
        lock_key = f"{pair_key}:lock"
        try:
            acquired = client.set(
                lock_key,
                token,
                nx=True,
                px=max(1000, int(float(getattr(self.settings, "reranker_cache_lock_ttl_seconds", 120)) * 1000)),
            )
            return token if acquired else None
        except Exception as exc:  # noqa: BLE001
            _metric("redis_errors")
            if is_redis_transport_error(exc):
                mark_redis_unavailable(exc)
            return "local-only"

    def _wait_for_pair(self, item: CacheCandidate, context: RerankCacheContext) -> tuple[dict[str, Any] | None, str | None]:
        _metric("lock_waits")
        deadline = time.monotonic() + max(0.1, float(getattr(self.settings, "reranker_cache_lock_wait_seconds", 30)))
        interval = 0.05
        while time.monotonic() < deadline:
            value, layer = self._get_json(item.pair_key)
            if self._valid_pair(value, item, context) is not None:
                return value, layer
            time.sleep(interval)
            interval = min(interval * 1.5, 0.5)
        return None, None

    def _release_distributed_lock(self, pair_key: str, token: str) -> None:
        if token == "local-only":
            return
        client = self._get_redis()
        if client is None:
            return
        lock_key = f"{pair_key}:lock"
        try:
            if hasattr(client, "eval"):
                client.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                    1,
                    lock_key,
                    token,
                )
            elif client.get(lock_key) == token:
                client.delete(lock_key)
        except Exception as exc:  # noqa: BLE001
            _metric("redis_errors")
            if is_redis_transport_error(exc):
                mark_redis_unavailable(exc)

    def _get_local_lock(self, key: str) -> threading.Lock:
        with self._local_locks_guard:
            entry = self._local_locks.get(key)
            if entry is None:
                lock = threading.Lock()
                self._local_locks[key] = (lock, 1)
            else:
                lock, count = entry
                self._local_locks[key] = (lock, count + 1)
            return lock

    def _release_local_lock(self, key: str, lock: threading.Lock) -> None:
        with self._local_locks_guard:
            entry = self._local_locks.get(key)
            if entry is None or entry[0] is not lock:
                return
            _, count = entry
            if count <= 1:
                self._local_locks.pop(key, None)
            else:
                self._local_locks[key] = (lock, count - 1)

    def _key(self, *parts: str) -> str:
        return redis_key(
            getattr(self.settings, "reranker_cache_namespace", "careeragent"),
            getattr(self.settings, "app_env", "development"),
            *parts,
        )

    @staticmethod
    def _candidate_id(candidate: dict[str, Any], index: int) -> str:
        for key in ("uid", "id", "chunk_uid", "job_id", "candidate_id"):
            value = candidate.get(key)
            if value is not None and str(value).strip():
                return str(value)
        return f"content:{index}"

    @staticmethod
    def _scope_payload(metadata: dict[str, Any], source_type: str) -> dict[str, Any]:
        lowered = source_type.lower()
        private = any(marker in lowered for marker in ("resume", "profile", "interview", "private"))
        identifiers = {
            key: str(metadata.get(key)).strip()
            for key in ("tenant_id", "user_id", "profile_id", "job_id")
            if metadata.get(key) is not None and str(metadata.get(key)).strip()
        }
        # A job_id alone describes a public job document, not a tenant's
        # private data. Keep it out of the private scope so identical public
        # evidence can be reused across result sets. Profile/user identifiers
        # always force isolation, and job_id remains part of that scope.
        private_identifiers = {key: value for key, value in identifiers.items() if key != "job_id"}
        if private or private_identifiers:
            return {"classification": "private", **identifiers}
        return {"classification": "public", "source_type": source_type}
