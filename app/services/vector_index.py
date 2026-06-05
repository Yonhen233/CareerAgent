import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import JobChunk, ResumeChunk
from app.services.text_splitter import TextChunk


TOKEN_RE = re.compile(r"[a-zA-Z0-9_\-\+#\.]{2,}|[\u4e00-\u9fff]{1,}")

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


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


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


class SQLiteVectorIndex:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.vector_library = ChromaVectorLibrary(self.settings.chroma_path) if self.settings.vector_backend != "sqlite" else None

    @property
    def dimensions(self) -> int:
        return self.settings.embedding_dimensions

    def upsert_profile_chunks(self, db: Session, profile_id: int, chunks: list[TextChunk]) -> int:
        db.query(ResumeChunk).filter(ResumeChunk.profile_id == profile_id).delete()
        inserted = 0
        for chunk in chunks:
            text = chunk.text.strip()
            if not text:
                continue
            row = ResumeChunk(
                profile_id=profile_id,
                chunk_uid=chunk.uid,
                chunk_type=chunk.chunk_type,
                source=chunk.source,
                text=text,
                token_count=len(tokenize(text)),
                embedding_json=hash_embedding(text, self.dimensions),
                metadata_json=chunk.metadata or {},
            )
            db.add(row)
            inserted += 1
        db.commit()
        if self.vector_library:
            self.vector_library.upsert(
                collection_name=f"profile_{profile_id}_chunks",
                ids=[chunk.uid for chunk in chunks if chunk.text.strip()],
                documents=[chunk.text.strip() for chunk in chunks if chunk.text.strip()],
                metadatas=[chunk.metadata or {} for chunk in chunks if chunk.text.strip()],
                embeddings=[hash_embedding(chunk.text.strip(), self.dimensions) for chunk in chunks if chunk.text.strip()],
            )
        return inserted

    def query_profile_chunks(self, db: Session, profile_id: int, query_text: str, top_k: int = 8) -> list[RetrievedChunk]:
        query_text = query_text.strip()
        if not query_text:
            return []

        expanded_query = expand_query_text(query_text)
        query_vec = hash_embedding(expanded_query, self.dimensions)
        rows = db.query(ResumeChunk).filter(ResumeChunk.profile_id == profile_id).all()
        scored: list[RetrievedChunk] = []
        query_tokens = set(tokenize(expanded_query))
        for row in rows:
            vector_score = cosine_similarity(query_vec, row.embedding_json or [])
            chunk_tokens = set(tokenize(row.text))
            lexical_score = len(query_tokens & chunk_tokens) / max(len(query_tokens), 1)
            type_boost = 0.05 if row.chunk_type in {"project", "experience", "skill"} else 0.0
            score = round(vector_score * 0.15 + lexical_score * 0.80 + type_boost, 6)
            scored.append(
                RetrievedChunk(
                    chunk_id=row.id,
                    chunk_uid=row.chunk_uid,
                    text=row.text,
                    chunk_type=row.chunk_type,
                    source=row.source,
                    score=score,
                    metadata=row.metadata_json or {},
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]

    def upsert_job_chunks(self, db: Session, job_id: int, chunks: list[TextChunk]) -> int:
        db.query(JobChunk).filter(JobChunk.job_id == job_id).delete()
        inserted = 0
        valid_chunks = [chunk for chunk in chunks if chunk.text.strip()]
        for chunk in valid_chunks:
            text = chunk.text.strip()
            row = JobChunk(
                job_id=job_id,
                chunk_uid=chunk.uid,
                chunk_type=chunk.chunk_type,
                source=chunk.source,
                text=text,
                token_count=len(tokenize(text)),
                embedding_json=hash_embedding(text, self.dimensions),
                metadata_json=chunk.metadata or {},
            )
            db.add(row)
            inserted += 1
        db.commit()
        if self.vector_library:
            self.vector_library.upsert(
                collection_name=f"job_{job_id}_chunks",
                ids=[chunk.uid for chunk in valid_chunks],
                documents=[chunk.text.strip() for chunk in valid_chunks],
                metadatas=[chunk.metadata or {} for chunk in valid_chunks],
                embeddings=[hash_embedding(chunk.text.strip(), self.dimensions) for chunk in valid_chunks],
            )
        return inserted

    def query_job_chunks(self, db: Session, job_id: int, query_text: str, top_k: int = 8) -> list[RetrievedChunk]:
        query_text = query_text.strip()
        if not query_text:
            return []

        expanded_query = expand_query_text(query_text)
        query_vec = hash_embedding(expanded_query, self.dimensions)
        rows = db.query(JobChunk).filter(JobChunk.job_id == job_id).all()
        scored: list[RetrievedChunk] = []
        query_tokens = set(tokenize(expanded_query))
        for row in rows:
            vector_score = cosine_similarity(query_vec, row.embedding_json or [])
            chunk_tokens = set(tokenize(row.text))
            lexical_score = len(query_tokens & chunk_tokens) / max(len(query_tokens), 1)
            type_boost = 0.05 if row.chunk_type in {"required_skills", "responsibilities", "qualifications"} else 0.0
            score = round(vector_score * 0.15 + lexical_score * 0.80 + type_boost, 6)
            scored.append(
                RetrievedChunk(
                    chunk_id=row.id,
                    chunk_uid=row.chunk_uid,
                    text=row.text,
                    chunk_type=row.chunk_type,
                    source=row.source,
                    score=score,
                    metadata=row.metadata_json or {},
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]


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
        collection = self.client.get_or_create_collection(name=collection_name)
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
