import hashlib
import math
import os
import re
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings, get_settings


TOKEN_RE = re.compile(r"[a-zA-Z0-9_\-\+#\.]{2,}|[\u4e00-\u9fff]+")
CJK_RE = re.compile(r"^[\u4e00-\u9fff]+$")

QUERY_ALIASES = {
    "retrieval augmented generation": "RAG",
    "python api service": "FastAPI",
    "embedded relational storage": "SQLite",
    "autonomous workflow orchestration": "Agent",
    "model quality measurement": "Evaluation",
    "safety checks": "Guardrails",
    "component based user interface": "React",
    "typed frontend code": "TypeScript",
    "deep learning framework": "PyTorch",
    "scheduled data pipelines": "Airflow",
}

_MODEL_CACHE: dict[str, Any] = {}
_MODEL_FAILURES: dict[str, str] = {}
_VECTOR_CACHE: dict[tuple[str, str, str], list[float]] = {}


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    provider: str
    model: str
    dimensions: int
    fallback_reason: str | None = None

    def info(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self.provider,
            "model": self.model,
            "dimensions": self.dimensions,
        }
        if self.fallback_reason:
            payload["fallback_reason"] = self.fallback_reason
        return payload


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text or ""):
        token = raw.lower()
        if not CJK_RE.fullmatch(token):
            tokens.append(token)
            continue
        if len(token) <= 3:
            tokens.append(token)
        for width in (2, 3):
            if len(token) < width:
                continue
            tokens.extend(token[index : index + width] for index in range(len(token) - width + 1))
    return tokens


def expand_query_text(text: str) -> str:
    expanded = text or ""
    lowered = expanded.lower()
    additions = []
    for phrase, alias in QUERY_ALIASES.items():
        if phrase in lowered and alias.lower() not in lowered:
            additions.append(alias)
    if additions:
        expanded = expanded + "\n" + " ".join(additions)
    return expanded


def hash_embedding(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign

    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=False))


class EmbeddingService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        provider: str | None = None,
        model_name: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = (provider or self.settings.embedding_provider).strip().lower()
        self.model_name = model_name or self.settings.embedding_model_name

    @property
    def dimensions(self) -> int:
        if self.provider in {"hash", "deterministic_hash", "offline"}:
            return self.settings.embedding_dimensions
        return self.settings.embedding_dimensions

    def embed_text(self, text: str) -> EmbeddingBatch:
        return self.embed_texts([text])

    def embed_texts(self, texts: list[str]) -> EmbeddingBatch:
        clean_texts = [text or "" for text in texts]
        if not clean_texts:
            return EmbeddingBatch(vectors=[], provider=self.provider, model=self.model_name, dimensions=0)

        if self.provider in {"hash", "deterministic_hash", "offline"}:
            return self._hash_batch(clean_texts)

        if self.provider in {"sentence_transformers", "sentence-transformer", "hf", "huggingface"}:
            try:
                return self._sentence_transformer_batch(clean_texts)
            except Exception as exc:  # noqa: BLE001
                if self.settings.embedding_provider_fallback.lower() == "hash":
                    return self._hash_batch(
                        clean_texts,
                        provider="hash_fallback",
                        model=f"hash-{self.settings.embedding_dimensions}",
                        fallback_reason=f"{self.provider}:{self.model_name} unavailable: {exc}",
                    )
                raise

        if self.settings.embedding_provider_fallback.lower() == "hash":
            return self._hash_batch(
                clean_texts,
                provider="hash_fallback",
                model=f"hash-{self.settings.embedding_dimensions}",
                fallback_reason=f"Unsupported embedding provider: {self.provider}",
            )
        raise ValueError(f"Unsupported embedding provider: {self.provider}")

    def _hash_batch(
        self,
        texts: list[str],
        *,
        provider: str = "hash",
        model: str | None = None,
        fallback_reason: str | None = None,
    ) -> EmbeddingBatch:
        model_name = model or f"hash-{self.settings.embedding_dimensions}"
        vectors = [hash_embedding(text, self.settings.embedding_dimensions) for text in texts]
        return EmbeddingBatch(
            vectors=vectors,
            provider=provider,
            model=model_name,
            dimensions=self.settings.embedding_dimensions,
            fallback_reason=fallback_reason,
        )

    def _sentence_transformer_batch(self, texts: list[str]) -> EmbeddingBatch:
        cached_vectors: list[list[float] | None] = []
        missing_texts: list[str] = []
        missing_positions: list[int] = []
        for index, text in enumerate(texts):
            cache_key = ("sentence_transformers", self.model_name, text)
            cached = _VECTOR_CACHE.get(cache_key)
            cached_vectors.append(cached)
            if cached is None:
                missing_texts.append(text)
                missing_positions.append(index)

        if missing_texts:
            model = self._load_sentence_transformer()
            raw_vectors = model.encode(
                missing_texts,
                batch_size=self.settings.embedding_batch_size,
                normalize_embeddings=self.settings.embedding_normalize,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            for position, raw_vector in zip(missing_positions, raw_vectors, strict=False):
                vector = [float(value) for value in raw_vector.tolist()]
                cached_vectors[position] = vector
                _VECTOR_CACHE[("sentence_transformers", self.model_name, texts[position])] = vector

        vectors = [vector or [] for vector in cached_vectors]
        dimensions = len(vectors[0]) if vectors and vectors[0] else 0
        return EmbeddingBatch(
            vectors=vectors,
            provider="sentence_transformers",
            model=self.model_name,
            dimensions=dimensions,
        )

    def _load_sentence_transformer(self) -> Any:
        cache_key = self.model_name
        if cache_key in _MODEL_FAILURES:
            raise RuntimeError(_MODEL_FAILURES[cache_key])
        if cache_key in _MODEL_CACHE:
            return _MODEL_CACHE[cache_key]
        try:
            self._ensure_local_model_cache_env()
            from sentence_transformers import SentenceTransformer  # type: ignore

            self.settings.embedding_cache_path.mkdir(parents=True, exist_ok=True)
            model = SentenceTransformer(
                self.model_name,
                cache_folder=str(self.settings.embedding_cache_path),
            )
            _MODEL_CACHE[cache_key] = model
            return model
        except Exception as exc:  # noqa: BLE001
            _MODEL_FAILURES[cache_key] = str(exc)
            raise

    def _ensure_local_model_cache_env(self) -> None:
        self.settings.embedding_cache_path.mkdir(parents=True, exist_ok=True)
        hf_home = self.settings.embedding_cache_path / "huggingface"
        hf_home.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(hf_home))
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(self.settings.embedding_cache_path))
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
