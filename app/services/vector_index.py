from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import JobChunk, ResumeChunk
from app.services.embedding_service import (
    EmbeddingBatch,
    EmbeddingService,
    cosine_similarity,
    expand_query_text,
    tokenize,
)
from app.services.evidence_classifier import EvidenceClassifier
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
        self.evidence_classifier = EvidenceClassifier()
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
        retrieval_texts = [
            self._retrieval_text(text, chunk.metadata or {})
            for text, chunk in zip(texts, valid_chunks, strict=False)
        ]
        embeddings = self.embedding_service.embed_texts(retrieval_texts)
        inserted = 0
        for chunk, vector in zip(valid_chunks, embeddings.vectors, strict=False):
            text = chunk.text.strip()
            metadata = self._metadata_with_embedding(chunk.metadata or {}, embeddings)
            metadata["embedding_text_version"] = "retrieval_context_v1"
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
                documents=retrieval_texts,
                metadatas=[self._chroma_metadata(chunk.metadata or {}, embeddings) for chunk in valid_chunks],
                embeddings=embeddings.vectors,
            )
        return inserted

    def query_profile_chunks(self, db: Session, profile_id: int, query_text: str, top_k: int = 8) -> list[RetrievedChunk]:
        rows = db.query(ResumeChunk).filter(ResumeChunk.profile_id == profile_id).all()
        ranked = self._query_rows(
            db=db,
            rows=rows,
            query_text=query_text,
            top_k=max(top_k, self.settings.reranker_top_n),
            type_boost_chunks={"project", "experience", "skill"},
        )
        return self._deduplicate_fact_views(
            self._apply_evidence_quality_prior(ranked),
            top_k=top_k,
        )

    def query_profile_chunks_multi(
        self,
        db: Session,
        profile_id: int,
        query_texts: list[str],
        *,
        top_k: int = 8,
        allowed_chunk_types: set[str] | None = None,
    ) -> list[RetrievedChunk]:
        queries = list(dict.fromkeys(text.strip() for text in query_texts if text and text.strip()))
        if not queries:
            return []
        if (not self.settings.rag_multi_query_enabled or len(queries) == 1) and not allowed_chunk_types:
            return self.query_profile_chunks(db, profile_id, queries[0], top_k=top_k)

        rows_query = db.query(ResumeChunk).filter(ResumeChunk.profile_id == profile_id)
        if allowed_chunk_types:
            rows_query = rows_query.filter(ResumeChunk.chunk_type.in_(allowed_chunk_types))
        rows = rows_query.all()
        if not self.settings.rag_multi_query_enabled or len(queries) == 1:
            ranked = self._query_rows(
                db=db,
                rows=rows,
                query_text=queries[0],
                top_k=max(top_k, self.settings.reranker_top_n),
                type_boost_chunks={"project", "experience", "skill"},
            )
            return self._deduplicate_fact_views(
                self._apply_evidence_quality_prior(ranked),
                top_k=top_k,
            )
        first_stage_limit = max(top_k, self.settings.reranker_top_n)
        ranked_lists = [
            self._query_rows(
                db=db,
                rows=rows,
                query_text=query,
                top_k=first_stage_limit,
                type_boost_chunks={"project", "experience", "skill"},
                rerank=False,
            )
            for query in queries
        ]
        by_uid: dict[str, RetrievedChunk] = {}
        rrf_scores: dict[str, float] = {}
        query_hits: dict[str, list[int]] = {}
        for query_index, ranked in enumerate(ranked_lists):
            for rank, chunk in enumerate(ranked, start=1):
                current = by_uid.get(chunk.chunk_uid)
                if current is None or chunk.score > current.score:
                    by_uid[chunk.chunk_uid] = chunk
                rrf_scores[chunk.chunk_uid] = rrf_scores.get(chunk.chunk_uid, 0.0) + 1.0 / (
                    self.settings.rag_multi_query_rrf_k + rank
                )
                query_hits.setdefault(chunk.chunk_uid, []).append(query_index)
        max_rrf = max(rrf_scores.values(), default=1.0)
        fused: list[RetrievedChunk] = []
        for uid, chunk in by_uid.items():
            normalized_rrf = rrf_scores[uid] / max(max_rrf, 1e-9)
            fused_score = round(float(chunk.score) * 0.70 + normalized_rrf * 0.30, 6)
            metadata = dict(chunk.metadata or {})
            retrieval = dict(metadata.get("retrieval") or {})
            retrieval["multi_query"] = {
                "strategy": "rrf_then_single_rerank",
                "query_count": len(queries),
                "hit_count": len(query_hits.get(uid, [])),
                "query_indexes": query_hits.get(uid, []),
                "rrf_k": self.settings.rag_multi_query_rrf_k,
                "rrf_score": round(rrf_scores[uid], 8),
                "rrf_score_normalized": round(normalized_rrf, 6),
            }
            metadata["retrieval"] = retrieval
            fused.append(replace(chunk, score=fused_score, metadata=metadata))
        fused.sort(key=lambda item: item.score, reverse=True)
        candidates = fused[:first_stage_limit]
        if self.settings.reranker_enabled:
            rerank_query = self._multi_query_rerank_text(queries)
            ranked = self.reranker.rerank_chunks(rerank_query, candidates, top_k=first_stage_limit)
            return self._deduplicate_fact_views(
                self._apply_evidence_quality_prior(ranked),
                top_k=top_k,
            )
        return self._deduplicate_fact_views(
            self._apply_evidence_quality_prior(candidates),
            top_k=top_k,
        )

    def _apply_evidence_quality_prior(self, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        """Soft-rank resume evidence by whether it can support a grounded claim.

        Semantic retrieval and reranking have already selected the candidates.
        This final pass only adds the small classifier prior and records the
        reason in metadata, so callers can audit why an explicit negative or a
        planned-learning chunk moved down the list.
        """
        enriched: list[RetrievedChunk] = []
        for candidate in candidates:
            prior, classification = self.evidence_classifier.retrieval_prior(
                candidate.text,
                chunk_type=candidate.chunk_type,
                source=candidate.source,
            )
            metadata = dict(candidate.metadata or {})
            retrieval = dict(metadata.get("retrieval") or {})
            retrieval["evidence_quality_prior"] = round(prior, 6)
            retrieval["evidence_classification"] = classification.as_dict()
            retrieval["evidence_quality_version"] = "soft_prior_v1"
            metadata["retrieval"] = retrieval
            enriched.append(replace(candidate, score=round(float(candidate.score) + prior, 6), metadata=metadata))
        enriched.sort(key=lambda item: item.score, reverse=True)
        return enriched

    @staticmethod
    def _deduplicate_fact_views(candidates: list[RetrievedChunk], *, top_k: int) -> list[RetrievedChunk]:
        """Count one fact once while retaining its distinct source details."""
        output: list[RetrievedChunk] = []
        fact_positions: dict[str, int] = {}
        for candidate in candidates:
            metadata = candidate.metadata or {}
            fact_id = str(metadata.get("fact_id") or "").strip()
            if fact_id and fact_id in fact_positions:
                position = fact_positions[fact_id]
                output[position] = SQLiteVectorIndex._merge_fact_views(output[position], candidate)
                continue
            if len(output) >= top_k:
                continue
            if fact_id:
                fact_positions[fact_id] = len(output)
            output.append(candidate)
        return output

    @staticmethod
    def _merge_fact_views(primary: RetrievedChunk, detail: RetrievedChunk) -> RetrievedChunk:
        metadata = dict(primary.metadata or {})
        views = list(metadata.get("evidence_views") or [])
        known_uids = {str(item.get("chunk_uid") or "") for item in views if isinstance(item, dict)}
        for item in (primary, detail):
            if item.chunk_uid in known_uids:
                continue
            item_metadata = item.metadata or {}
            views.append({
                "chunk_uid": item.chunk_uid,
                "source": item.source,
                "chunk_type": item.chunk_type,
                "text": item.text[:900],
                "score": round(float(item.score), 6),
                "evidence_scope": item_metadata.get("evidence_scope"),
                "fact_links": item_metadata.get("fact_links") or [],
            })
            known_uids.add(item.chunk_uid)
        metadata["evidence_views"] = views[:4]
        metadata["evidence_view_count"] = len(views)

        texts = list(dict.fromkeys(
            item["text"].strip()
            for item in views
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ))
        combined_text = "\n\n[同一经历的原文细节]\n".join(texts)[:2400]
        return replace(
            primary,
            text=combined_text or primary.text,
            score=max(float(primary.score), float(detail.score)),
            metadata=metadata,
        )

    @staticmethod
    def _multi_query_rerank_text(queries: list[str]) -> str:
        # Rerank against every retrieval perspective; no single variant is privileged.
        compact = [" ".join(query.split())[:220] for query in queries if query.strip()]
        return "\n".join(dict.fromkeys(compact))[:660]

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
            db=db,
            rows=rows,
            query_text=query_text,
            top_k=top_k,
            type_boost_chunks={"required_skills", "responsibilities", "qualifications"},
        )

    def query_job_corpus(
        self,
        db: Session,
        query_text: str,
        *,
        job_ids: set[int] | None = None,
        top_k: int = 80,
        rerank: bool = True,
    ) -> list[RetrievedChunk]:
        query = db.query(JobChunk)
        if job_ids is not None:
            if not job_ids:
                return []
            query = query.filter(JobChunk.job_id.in_(job_ids))
        rows = query.all()
        job_by_chunk_id = {row.id: row.job_id for row in rows}
        results = self._query_rows(
            db=db,
            rows=rows,
            query_text=query_text,
            top_k=top_k,
            type_boost_chunks={"required_skills", "responsibilities", "qualifications", "preferred_skills"},
            rerank=rerank,
        )
        for item in results:
            metadata = dict(item.metadata or {})
            metadata["job_id"] = job_by_chunk_id.get(item.chunk_id)
            item.metadata = metadata
        return results

    def _query_rows(
        self,
        *,
        db: Session,
        rows: list[Any],
        query_text: str,
        top_k: int,
        type_boost_chunks: set[str],
        rerank: bool = True,
    ) -> list[RetrievedChunk]:
        query_text = query_text.strip()
        if not query_text:
            return []

        expanded_query = expand_query_text(query_text)
        query_embedding = self.embedding_service.embed_text(expanded_query)
        query_vec = query_embedding.vectors[0] if query_embedding.vectors else []
        row_vectors, migrated = self._row_vectors(rows, expected_dimensions=len(query_vec))
        if migrated:
            db.commit()
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
        use_reranker = self.settings.reranker_enabled and rerank
        first_stage_limit = max(top_k, self.settings.reranker_top_n if use_reranker else top_k)
        candidates = scored[:first_stage_limit]
        if use_reranker:
            return self.reranker.rerank_chunks(expanded_query, candidates, top_k=top_k)
        return candidates[:top_k]

    def _row_vectors(
        self,
        rows: list[Any],
        *,
        expected_dimensions: int,
    ) -> tuple[list[list[float]], int]:
        vectors: list[list[float] | None] = []
        missing_texts: list[str] = []
        missing_positions: list[int] = []
        for index, row in enumerate(rows):
            vector = row.embedding_json or []
            metadata = dict(row.metadata_json or {})
            expected_version = "retrieval_context_v1" if metadata.get("retrieval_context") else None
            version_matches = not expected_version or metadata.get("embedding_text_version") == expected_version
            if expected_dimensions and len(vector) == expected_dimensions and version_matches:
                vectors.append(vector)
                continue
            vectors.append(None)
            missing_texts.append(self._retrieval_text(row.text, metadata))
            missing_positions.append(index)

        if missing_texts:
            recomputed = self.embedding_service.embed_texts(missing_texts)
            for position, vector in zip(missing_positions, recomputed.vectors, strict=False):
                vectors[position] = vector
                row = rows[position]
                row.embedding_json = vector
                metadata = dict(row.metadata_json or {})
                metadata["embedding"] = recomputed.info()
                if metadata.get("retrieval_context"):
                    metadata["embedding_text_version"] = "retrieval_context_v1"
                row.metadata_json = metadata
        return [vector or [] for vector in vectors], len(missing_positions)

    @staticmethod
    def _retrieval_text(text: str, metadata: dict[str, Any]) -> str:
        context = str(metadata.get("retrieval_context") or "").strip()
        if not context or context in text:
            return text
        return f"[简历上下文] {context}\n[当前证据] {text}"

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
