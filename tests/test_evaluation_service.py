import asyncio
import json
from pathlib import Path

from app.services.job_sources import JobPosting
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


def test_agent_full_flow_evaluation_covers_orchestrator_components(db_session):
    run = asyncio.run(EvaluationService().run_agent_full_flow_evaluation(db_session))

    assert run.summary_json["evaluation_type"] == "agent_full_flow"
    assert run.summary_json["case_count"] >= 6
    assert run.summary_json["pass_rate"] == 1.0
    assert run.summary_json["top_job_accuracy"] == 1.0
    assert run.summary_json["score_gate_accuracy"] == 1.0
    assert run.summary_json["quick_apply_pass_rate"] == 1.0
    assert run.summary_json["trace_pass_rate"] == 1.0
    assert run.summary_json["artifact_pass_rate"] == 1.0
    assert run.summary_json["fit_gate_block_count"] >= 3
    assert any(item.get("fit_gate_blocked") for item in run.case_results_json)
    assert all(item.get("run_trace") for item in run.case_results_json)


def test_real_job_source_smoke_records_source_layer_metrics(db_session):
    class HealthySource:
        name = "healthy"

        async def search(self, *, query: str, location: str | None, limit: int):
            return [
                JobPosting(
                    source=self.name,
                    external_id="healthy-1",
                    title="Agent Development Intern",
                    company="Example AI",
                    location=location or "Shanghai",
                    job_type="internship",
                    apply_url="https://example.com/jobs/healthy-1",
                    raw_jd_text=f"{query}: build Agent workflows with FastAPI and RAG.",
                )
            ][:limit]

    class BrokenSource:
        name = "broken"

        async def search(self, *, query: str, location: str | None, limit: int):
            raise RuntimeError("source timeout")

    class FakeRegistry:
        def select(self, names=None):
            return [HealthySource(), BrokenSource()]

    run = asyncio.run(
        EvaluationService().run_real_job_source_smoke(
            db_session,
            query="Agent Development Intern",
            location="Shanghai",
            limit=5,
            source_registry=FakeRegistry(),
        )
    )

    assert run.summary_json["evaluation_type"] == "real_job_source_smoke"
    assert run.summary_json["status"] == "completed_with_source_errors"
    assert run.summary_json["reachable_source_rate"] == 0.5
    assert run.summary_json["result_source_rate"] == 0.5
    assert run.summary_json["total_result_count"] == 1
    assert run.summary_json["non_empty_jd_rate"] == 1.0
    assert run.summary_json["apply_url_rate"] == 1.0
    assert run.summary_json["query_relevance_rate"] == 1.0
    assert run.summary_json["agent_related_rate"] == 1.0
    assert run.summary_json["core_regression_independent"] is True
    assert any(item["status"] == "source_error" and item["error"] for item in run.case_results_json)
    assert run.case_results_json[0]["sample_jobs"][0]["agent_related"] is True


def test_real_job_source_smoke_marks_empty_sources(db_session):
    class HealthySource:
        name = "healthy"

        async def search(self, *, query: str, location: str | None, limit: int):
            return [
                JobPosting(
                    source=self.name,
                    external_id="healthy-1",
                    title="Agent Development Intern",
                    company="Example AI",
                    location=location,
                    job_type="internship",
                    apply_url="https://example.com/jobs/healthy-1",
                    raw_jd_text="Build Agent workflows.",
                )
            ]

    class EmptySource:
        name = "empty"

        async def search(self, *, query: str, location: str | None, limit: int):
            return []

    class FakeRegistry:
        def select(self, names=None):
            return [HealthySource(), EmptySource()]

    run = asyncio.run(
        EvaluationService().run_real_job_source_smoke(
            db_session,
            source_registry=FakeRegistry(),
        )
    )

    assert run.summary_json["status"] == "completed_with_empty_sources"
    assert run.summary_json["reachable_source_rate"] == 1.0
    assert run.summary_json["result_source_rate"] == 0.5


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
            "fit_context_compression": {"reduction_ratio": 0.4},
            "tailor_context_compression": {"reduction_ratio": 0.5, "retained_evidence_count": 8},
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
    assert summary["context_compression"]["fit_context_count"] == 1
    assert summary["context_compression"]["avg_tailor_reduction_ratio"] == 0.5
    assert "difficulty_breakdown" in summary


def test_forbidden_claim_hits_ignore_negated_disclosures():
    service = EvaluationService()

    conservative_text = "Built metric dashboards. Did not implement ranking models or CTR features."
    assert service._forbidden_claim_hits(conservative_text, ["ranking model", "CTR feature"]) == []

    inflated_text = "Implemented ranking models and owned CTR feature engineering for recommender systems."
    assert service._forbidden_claim_hits(inflated_text, ["ranking model", "CTR feature engineering"]) == [
        "ranking model",
        "CTR feature engineering",
    ]


def test_llm_workflow_resume_loads_completed_prefix():
    service = EvaluationService()
    trace_path = Path("data/runtime/test_llm_resume_prefix.jsonl")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "type": "llm_workflow_case_result",
            "case_result": {
                "name": "case_a",
                "status": "completed",
                "case_passed": True,
                "stage_trace": [{"stage": "case", "status": "completed"}],
            },
        },
        {
            "type": "llm_workflow_case_result",
            "case_result": {
                "name": "case_b",
                "status": "completed",
                "case_passed": False,
                "stage_trace": [{"stage": "case", "status": "completed"}],
            },
        },
    ]
    try:
        trace_path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
        selected = [{"name": "case_a"}, {"name": "case_b"}, {"name": "case_c"}]

        loaded = service._load_resumable_llm_results(trace_path, selected)

        assert [item["name"] for item in loaded] == ["case_a", "case_b"]
        assert loaded[1]["case_passed"] is False
    finally:
        trace_path.unlink(missing_ok=True)


def test_llm_workflow_resume_stops_at_first_missing_case():
    service = EvaluationService()
    trace_path = Path("data/runtime/test_llm_resume_missing.jsonl")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        trace_path.write_text(
            json.dumps(
                {
                    "type": "llm_workflow_case_result",
                    "case_result": {"name": "case_b", "status": "completed", "case_passed": True},
                }
            ),
            encoding="utf-8",
        )

        loaded = service._load_resumable_llm_results(trace_path, [{"name": "case_a"}, {"name": "case_b"}])

        assert loaded == []
    finally:
        trace_path.unlink(missing_ok=True)
