import asyncio

from app.services.evaluation_service import EvaluationService


def test_sample_evaluation_produces_quantitative_metrics(db_session):
    run = asyncio.run(EvaluationService().run_sample_evaluation(db_session))

    assert run.summary_json["case_count"] >= 3
    assert 0 <= run.summary_json["pass_rate"] <= 1
    assert "avg_required_skill_recall" in run.summary_json
    assert run.case_results_json[0]["overall_score"] >= 0


def test_pdf_chunk_strategy_evaluation_selects_strategy(db_session):
    run = EvaluationService().run_pdf_chunk_strategy_evaluation(db_session)

    assert run.summary_json["case_count"] >= 90
    assert run.summary_json["query_count"] >= 500
    assert run.summary_json["selected_strategy"]
    assert len(run.summary_json["strategy_results"]) >= 4
    assert "difficulty_breakdown" in run.summary_json["strategy_results"][0]
    assert "noise_breakdown" in run.summary_json["strategy_results"][0]


def test_rag_strategy_evaluation_selects_strategy(db_session):
    run = EvaluationService().run_rag_strategy_evaluation(db_session)

    assert run.summary_json["case_count"] >= 180
    assert run.summary_json["selected_strategy"]
    assert "vector_store_selection" in run.summary_json
    assert "embedding_model_selection" in run.summary_json
    assert "reranker_selection" in run.summary_json
    assert len(run.summary_json["strategy_results"]) >= 4
    assert any(item["uses_reranker"] for item in run.summary_json["strategy_results"])
    assert "difficulty_breakdown" in run.summary_json["strategy_results"][0]
