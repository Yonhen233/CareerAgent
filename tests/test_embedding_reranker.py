import os

from app.services.embedding_service import EmbeddingService, tokenize
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


def test_chinese_tokenizer_exposes_terms_inside_long_sentences():
    tokens = tokenize("请说明项目架构中的Agent编排位置和替代方案")

    assert {"架构", "编排", "替代", "方案"} <= set(tokens)
    assert "请说明项目架构中的" not in tokens


def test_chinese_heuristic_reranker_prefers_architecture_evidence():
    candidates = [
        {
            "uid": "evaluation",
            "text": "Agent 岗位排序评测包含九个案例和离线指标。",
            "chunk_type": "document_section",
            "score": 0.5,
        },
        {
            "uid": "architecture",
            "text": "项目架构中 Agent 负责工作流编排、工具路由和状态管理，不使用时可改为固定流程。",
            "chunk_type": "document_section",
            "score": 0.5,
        },
    ]

    reranked = RerankerService(enabled=True, provider="heuristic", anchor_top_n=0).rerank_dicts(
        "说明 Agent 在项目架构中的位置、选型理由和替代方案",
        candidates,
        top_k=2,
    )

    assert reranked[0]["uid"] == "architecture"


def test_english_cross_encoder_is_not_used_for_chinese_query(monkeypatch):
    service = RerankerService(
        enabled=True,
        provider="cross_encoder",
        model_name="cross-encoder/ms-marco-MiniLM-L-6-v2",
        anchor_top_n=0,
    )
    monkeypatch.setattr(
        service,
        "_load_cross_encoder",
        lambda: (_ for _ in ()).throw(AssertionError("English reranker must not score Chinese queries")),
    )

    reranked = service.rerank_dicts(
        "说明 Agent 在项目架构中的位置和替代方案",
        [
            {"uid": "eval", "text": "Agent 评测包含九个样例。", "chunk_type": "document_section", "score": 0.5},
            {
                "uid": "architecture",
                "text": "项目架构由 Agent 编排工具、状态和审批，替代方案是固定工作流。",
                "chunk_type": "document_section",
                "score": 0.5,
            },
        ],
        top_k=2,
    )

    assert reranked[0]["uid"] == "architecture"
    assert reranked[0]["metadata"]["rerank"]["reranker_provider"] == "heuristic"
    assert "English-only" in reranked[0]["metadata"]["rerank"]["fallback_reason"]


def test_chinese_language_route_preserves_high_confidence_first_stage_anchor():
    service = RerankerService(
        enabled=True,
        provider="cross_encoder",
        model_name="cross-encoder/ms-marco-MiniLM-L-6-v2",
        anchor_top_n=5,
    )
    candidates = [
        {
            "uid": f"noise-{index}",
            "text": "Agent 评测与通用输出治理。",
            "chunk_type": "document_section",
            "score": 0.9 - index * 0.01,
        }
        for index in range(5)
    ] + [
        {
            "uid": "fastapi",
            "text": "FastAPI 并发请求使用异步 I/O，trace 写入应避免阻塞并交给队列。",
            "chunk_type": "document_section",
            "score": 0.82,
        }
    ]

    reranked = service.rerank_dicts(
        "FastAPI 接口并发变高时 trace 怎么记录？",
        candidates,
        top_k=6,
    )

    assert [item["uid"] for item in reranked[:5]] == [f"noise-{index}" for index in range(5)]
    assert reranked[5]["uid"] == "fastapi"
    assert reranked[5]["metadata"]["rerank"]["language_route"] == "cjk_lexical"


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
