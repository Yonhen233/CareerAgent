import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import ResumeChunk
from app.services.text_splitter import TextChunk


TOKEN_RE = re.compile(r"[a-zA-Z0-9_\-\+#\.]{2,}|[\u4e00-\u9fff]{1,}")


@dataclass
class RetrievedChunk:
    chunk_id: int
    chunk_uid: str
    text: str
    chunk_type: str
    source: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "chunk_uid": self.chunk_uid,
            "text": self.text,
            "chunk_type": self.chunk_type,
            "source": self.source,
            "score": self.score,
        }


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


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
            )
            db.add(row)
            inserted += 1
        db.commit()
        return inserted

    def query_profile_chunks(self, db: Session, profile_id: int, query_text: str, top_k: int = 8) -> list[RetrievedChunk]:
        query_text = query_text.strip()
        if not query_text:
            return []

        query_vec = hash_embedding(query_text, self.dimensions)
        rows = db.query(ResumeChunk).filter(ResumeChunk.profile_id == profile_id).all()
        scored: list[RetrievedChunk] = []
        query_tokens = set(tokenize(query_text))
        for row in rows:
            vector_score = cosine_similarity(query_vec, row.embedding_json or [])
            chunk_tokens = set(tokenize(row.text))
            lexical_score = len(query_tokens & chunk_tokens) / max(len(query_tokens), 1)
            score = round(vector_score * 0.7 + lexical_score * 0.3, 6)
            scored.append(
                RetrievedChunk(
                    chunk_id=row.id,
                    chunk_uid=row.chunk_uid,
                    text=row.text,
                    chunk_type=row.chunk_type,
                    source=row.source,
                    score=score,
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:top_k]
