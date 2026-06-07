import os

from app.services.embedding_service import EmbeddingService
from app.services.reranker import RerankerService


def test_embedding_service_returns_vectors_with_metadata():
    batch = EmbeddingService(provider="hash").embed_texts(["FastAPI RAG Agent", "React CSS design system"])

    assert len(batch.vectors) == 2
    assert batch.provider == "hash"
    assert batch.dimensions > 0
    assert len(batch.vectors[0]) == batch.dimensions


def test_model_services_set_project_local_hf_cache(monkeypatch):
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("SENTENCE_TRANSFORMERS_HOME", raising=False)
    monkeypatch.delenv("HF_HUB_DISABLE_SYMLINKS_WARNING", raising=False)

    embedding_service = EmbeddingService(provider="sentence_transformers")
    embedding_service._ensure_local_model_cache_env()

    assert "CareerAgent" in os.environ["HF_HOME"]
    assert os.environ["HF_HOME"].endswith("data\\models\\huggingface") or os.environ["HF_HOME"].endswith(
        "data/models/huggingface"
    )
    assert os.environ["SENTENCE_TRANSFORMERS_HOME"].endswith("data\\models") or os.environ[
        "SENTENCE_TRANSFORMERS_HOME"
    ].endswith("data/models")
    assert os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] == "1"


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
