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


def test_cross_encoder_reranks_multiple_query_groups_in_one_predict_call(monkeypatch):
    class FakeCrossEncoder:
        calls = 0

        def predict(self, pairs, *, batch_size, show_progress_bar):
            del batch_size, show_progress_bar
            self.calls += 1
            return [1.0 if query.lower() in text.lower() else 0.1 for query, text in pairs]

    model = FakeCrossEncoder()
    service = RerankerService(enabled=True, provider="cross_encoder", anchor_top_n=0)
    monkeypatch.setattr(service, "_load_cross_encoder", lambda: model)
    groups = [
        (
            "RAG",
            [
                {"uid": "a", "text": "普通后端项目", "chunk_type": "project", "score": 0.5},
                {"uid": "b", "text": "RAG 检索与评测", "chunk_type": "project", "score": 0.5},
            ],
            2,
        ),
        (
            "FastAPI",
            [
                {"uid": "c", "text": "FastAPI 并发接口", "chunk_type": "project", "score": 0.5},
                {"uid": "d", "text": "离线数据处理", "chunk_type": "project", "score": 0.5},
            ],
            2,
        ),
    ]

    reranked = service.rerank_dict_groups(groups)

    assert model.calls == 1
    assert reranked[0][0]["uid"] == "b"
    assert reranked[1][0]["uid"] == "c"
    assert reranked[0][0]["metadata"]["rerank"]["batched_query_count"] == 2
    assert reranked[0][0]["metadata"]["rerank"]["batched_pair_count"] == 4
