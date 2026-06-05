import asyncio

from app.services.evaluation_service import EvaluationService


def test_sample_evaluation_produces_quantitative_metrics(db_session):
    run = asyncio.run(EvaluationService().run_sample_evaluation(db_session))

    assert run.summary_json["case_count"] >= 3
    assert 0 <= run.summary_json["pass_rate"] <= 1
    assert "avg_required_skill_recall" in run.summary_json
    assert run.case_results_json[0]["overall_score"] >= 0
