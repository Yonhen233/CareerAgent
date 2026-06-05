import asyncio
import json
from pathlib import Path

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


def test_llm_workflow_dataset_covers_full_pipeline():
    cases = json.loads(Path("evals/llm_workflow_cases.json").read_text(encoding="utf-8"))
    required_fields = {
        "name",
        "difficulty",
        "resume_raw_text",
        "expected_profile_skills",
        "expected_profile_keywords",
        "job",
        "expected_jd_skills",
        "expected_fit_label",
        "expected_fit_score_range",
        "run_tailor",
        "expected_tailored_keywords",
        "forbidden_tailored_claims",
    }

    assert len(cases) >= 18
    assert required_fields <= set(cases[0])
    assert {case["expected_fit_label"] for case in cases} == {"strong_fit", "partial_fit", "weak_fit"}
    assert sum(1 for case in cases if case["run_tailor"]) >= 10
    assert {"easy", "medium", "hard", "adversarial"} <= {case["difficulty"] for case in cases}


def test_llm_workflow_summary_has_quantitative_metrics():
    service = EvaluationService()
    case_results = [
        {
            "name": "strong_case",
            "difficulty": "easy",
            "run_tailor": True,
            "status": "completed",
            "case_passed": True,
            "resume_parse_success": True,
            "profile_skill_recall": 1.0,
            "profile_keyword_hit_rate": 0.8,
            "jd_parse_success": True,
            "jd_skill_recall": 1.0,
            "fit_judge_success": True,
            "label_passed": True,
            "fit_score_in_expected_range": True,
            "fit_score_range_error": 0,
            "matcher_evidence_hit_rate": 0.75,
            "tailor_success": True,
            "tailor_passed": True,
            "tailored_keyword_hit_rate": 0.83,
            "guardrail_passed": True,
            "forbidden_claim_free": True,
            "hallucination_count": 0,
        },
        {
            "name": "failed_case",
            "difficulty": "adversarial",
            "run_tailor": False,
            "status": "failed",
            "failed_stage": "jd_parse",
            "case_passed": False,
            "resume_parse_success": True,
            "profile_skill_recall": 0.5,
            "profile_keyword_hit_rate": 0.5,
        },
    ]

    summary = service._summarize_llm_workflow(case_results, Path("evals/llm_workflow_cases.json"))

    assert summary["case_count"] == 2
    assert summary["completed_rate"] == 0.5
    assert summary["end_to_end_pass_rate"] == 0.5
    assert summary["resume_parse_success_rate"] == 1.0
    assert summary["jd_parse_success_rate"] == 0.5
    assert summary["fit_label_accuracy"] == 1.0
    assert summary["tailor_pass_rate"] == 1.0
    assert summary["failed_stage_breakdown"] == {"jd_parse": 1}
    assert "difficulty_breakdown" in summary
