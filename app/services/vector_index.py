from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import JobChunk, ResumeChunk
from app.services.embedding_service import (
    EmbeddingBatch,
    EmbeddingService,
    cosine_similarity,
    expand_query_text,
    hash_embedding,
    tokenize,
)
from app.services.reranker import RerankerService
from app.services.text_splitter import TextChunk


@dataclass
class RetrievedChunk:
    chunk_id: int
    chunk_uid: str
    text: str
    chunk_type: str
    source: str
    score: float
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "chunk_uid": self.chunk_uid,
            "text": self.text,
            "chunk_type": self.chunk_type,
            "source": self.source,
            "score": self.score,
            "metadata": self.metadata or {},
        }


class SQLiteVectorIndex:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.embedding_service = EmbeddingService(settings=self.settings)
        self.reranker = RerankerService(settings=self.settings)
        self.vector_library = (
            ChromaVectorLibrary(self.settings.chroma_path) if self.settings.vector_backend != "sqlite" else None
        )

    @property
    def dimensions(self) -> int:
        return self.settings.embedding_dimensions

    def upsert_profile_chunks(self, db: Session, profile_id: int, chunks: list[TextChunk]) -> int:
        db.query(ResumeChunk).filter(ResumeChunk.profile_id == profile_id).delete()
        valid_chunks = [chunk for chunk in chunks if chunk.text.strip()]
        texts = [chunk.text.strip() for chunk in valid_chunks]
        embeddings = self.embedding_service.embed_texts(texts)
        inserted = 0
        for chunk, vector in zip(valid_chunks, embeddings.vectors, strict=False):
            text = chunk.text.strip()
            metadata = self._metadata_with_embedding(chunk.metadata or {}, embeddings)
            row = ResumeChunk(
                profile_id=profile_id,
                chunk_uid=chunk.uid,
                chunk_type=chunk.chunk_type,
                source=chunk.source,
                text=text,
                token_count=len(tokenize(text)),
                embedding_json=vector,
                metadata_json=metadata,
            )
            db.add(row)
            inserted += 1
        db.commit()
        if self.vector_library:
            self.vector_library.upsert(
                collection_name=f"profile_{profile_id}_chunks",
                ids=[chunk.uid for chunk in valid_chunks],
                documents=texts,
                metadatas=[self._chroma_metadata(chunk.metadata or {}, embeddings) for chunk in valid_chunks],
                embeddings=embeddings.vectors,
            )
        return inserted

    def query_profile_chunks(self, db: Session, profile_id: int, query_text: str, top_k: int = 8) -> list[RetrievedChunk]:
        rows = db.query(ResumeChunk).filter(ResumeChunk.profile_id == profile_id).all()
        return self._query_rows(
            rows=rows,
            query_text=query_text,
            top_k=top_k,
            type_boost_chunks={"project", "experience", "skill"},
        )

    def upsert_job_chunks(self, db: Session, job_id: int, chunks: list[TextChunk]) -> int:
        db.query(JobChunk).filter(JobChunk.job_id == job_id).delete()
        valid_chunks = [chunk for chunk in chunks if chunk.text.strip()]
        texts = [chunk.text.strip() for chunk in valid_chunks]
        embeddings = self.embedding_service.embed_texts(texts)
        inserted = 0
        for chunk, vector in zip(valid_chunks, embeddings.vectors, strict=False):
            text = chunk.text.strip()
            metadata = self._metadata_with_embedding(chunk.metadata or {}, embeddings)
            row = JobChunk(
                job_id=job_id,
                chunk_uid=chunk.uid,
                chunk_type=chunk.chunk_type,
                source=chunk.source,
                text=text,
                token_count=len(tokenize(text)),
                embedding_json=vector,
                metadata_json=metadata,
            )
            db.add(row)
            inserted += 1
        db.commit()
        if self.vector_library:
            self.vector_library.upsert(
                collection_name=f"job_{job_id}_chunks",
                ids=[chunk.uid for chunk in valid_chunks],
                documents=texts,
                metadatas=[self._chroma_metadata(chunk.metadata or {}, embeddings) for chunk in valid_chunks],
                embeddings=embeddings.vectors,
            )
        return inserted

    def query_job_chunks(self, db: Session, job_id: int, query_text: str, top_k: int = 8) -> list[RetrievedChunk]:
        rows = db.query(JobChunk).filter(JobChunk.job_id == job_id).all()
        return self._query_rows(
            rows=rows,
            query_text=query_text,
            top_k=top_k,
            type_boost_chunks={"required_skills", "responsibilities", "qualifications"},
        )

    def _query_rows(
        self,
        *,
        rows: list[Any],
        query_text: str,
        top_k: int,
        type_boost_chunks: set[str],
    ) -> list[RetrievedChunk]:
        query_text = query_text.strip()
        if not query_text:
            return []

        expanded_query = expand_query_text(query_text)
        query_embedding = self.embedding_service.embed_text(expanded_query)
        query_vec = query_embedding.vectors[0] if query_embedding.vectors else []
        row_vectors = self._row_vectors(rows, expected_dimensions=len(query_vec))
        query_tokens = set(tokenize(expanded_query))
        scored: list[RetrievedChunk] = []
        for row, row_vec in zip(rows, row_vectors, strict=False):
            vector_score = cosine_similarity(query_vec, row_vec)
            chunk_tokens = set(tokenize(row.text))
            lexical_score = len(query_tokens & chunk_tokens) / max(len(query_tokens), 1)
            type_boost = self.settings.retrieval_type_boost if row.chunk_type in type_boost_chunks else 0.0
            score = round(
                vector_score * self.settings.retrieval_vector_weight
                + lexical_score * self.settings.retrieval_lexical_weight
                + type_boost,
                6,
            )
            metadata = dict(row.metadata_json or {})
            metadata["retrieval"] = {
                "expanded_query": expanded_query if expanded_query != query_text else None,
                "query_embedding": query_embedding.info(),
                "vector_score": round(vector_score, 6),
                "lexical_score": round(lexical_score, 6),
                "type_boost": type_boost,
                "first_stage_score": score,
                "weights": {
                    "vector": self.settings.retrieval_vector_weight,
                    "lexical": self.settings.retrieval_lexical_weight,
                    "type_boost": self.settings.retrieval_type_boost,
                },
            }
            scored.append(
                RetrievedChunk(
                    chunk_id=row.id,
                    chunk_uid=row.chunk_uid,
                    text=row.text,
                    chunk_type=row.chunk_type,
                    source=row.source,
                    score=score,
                    metadata=metadata,
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        first_stage_limit = max(top_k, self.settings.reranker_top_n if self.settings.reranker_enabled else top_k)
        candidates = scored[:first_stage_limit]
        if self.settings.reranker_enabled:
            return self.reranker.rerank_chunks(expanded_query, candidates, top_k=top_k)
        return candidates[:top_k]

    def _row_vectors(self, rows: list[Any], *, expected_dimensions: int) -> list[list[float]]:
        vectors: list[list[float] | None] = []
        missing_texts: list[str] = []
        missing_positions: list[int] = []
        for index, row in enumerate(rows):
            vector = row.embedding_json or []
            if expected_dimensions and len(vector) == expected_dimensions:
                vectors.append(vector)
                continue
            vectors.append(None)
            missing_texts.append(row.text)
            missing_positions.append(index)

        if missing_texts:
            recomputed = self.embedding_service.embed_texts(missing_texts)
            for position, vector in zip(missing_positions, recomputed.vectors, strict=False):
                vectors[position] = vector
        return [vector or [] for vector in vectors]

    def _metadata_with_embedding(self, metadata: dict[str, Any], embeddings: EmbeddingBatch) -> dict[str, Any]:
        enriched = dict(metadata)
        enriched["embedding"] = embeddings.info()
        return enriched

    def _chroma_metadata(self, metadata: dict[str, Any], embeddings: EmbeddingBatch) -> dict[str, Any]:
        chroma_metadata = {
            str(key): value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
            for key, value in metadata.items()
        }
        chroma_metadata["embedding_provider"] = embeddings.provider
        chroma_metadata["embedding_model"] = embeddings.model
        chroma_metadata["embedding_dimensions"] = embeddings.dimensions
        return chroma_metadata


class ChromaVectorLibrary:
    def __init__(self, path) -> None:
        self.path = path
        self.available = False
        self.client = None
        try:
            import chromadb  # type: ignore

            self.path.mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=str(self.path))
            self.available = True
        except Exception:
            self.available = False

    def upsert(
        self,
        *,
        collection_name: str,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> None:
        if not self.available or self.client is None or not ids:
            return
        try:
            collection = self.client.get_or_create_collection(name=collection_name)
            collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
        except Exception:
            self.available = False
