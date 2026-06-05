from app.services.embedding_service import EmbeddingService
from app.services.reranker import RerankerService


def test_embedding_service_returns_vectors_with_metadata():
    batch = EmbeddingService(provider="hash").embed_texts(["FastAPI RAG Agent", "React CSS design system"])

    assert len(batch.vectors) == 2
    assert batch.provider == "hash"
    assert batch.dimensions > 0
    assert len(batch.vectors[0]) == batch.dimensions


def test_reranker_promotes_more_relevant_candidate():
    candidates = [
        {"uid": "frontend", "text": "React CSS Storybook component library", "chunk_type": "project", "score": 0.4},
        {"uid": "agent", "text": "FastAPI RAG Agent workflow with SQLite evaluation", "chunk_type": "project", "score": 0.4},
    ]

    reranked = RerankerService(enabled=True, provider="heuristic", anchor_top_n=0).rerank_dicts(
        "Agent RAG FastAPI",
        candidates,
        top_k=2,
    )

    assert reranked[0]["uid"] == "agent"
    assert reranked[0]["metadata"]["rerank"]["reranker_provider"] == "heuristic"
