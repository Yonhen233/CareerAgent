from pathlib import Path

from app.models.entities import EvaluationRun, LLMCallLog
from app.services.agent_system_evaluation import AgentSystemEvaluationReporter


def test_system_reporter_uses_provider_cache_details_for_cost(db_session):
    db_session.add_all(
        [
            LLMCallLog(
                trace_name="resume_parser.parse",
                model="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
                status="completed",
                prompt_preview_json={},
                response_preview="ok",
                latency_ms=100,
                prompt_chars=100,
                response_chars=2,
                prompt_tokens=1000,
                completion_tokens=200,
                total_tokens=1200,
                context_json={
                    "system_evaluation_id": "eval-a",
                    "model_route": "flash_economy",
                    "provider_usage": {
                        "prompt_cache_hit_tokens": 700,
                        "prompt_cache_miss_tokens": 300,
                    },
                },
            ),
            LLMCallLog(
                trace_name="interview_agentic_rag.verify",
                model="deepseek-v4-pro",
                base_url="https://api.deepseek.com",
                status="completed",
                prompt_preview_json={},
                response_preview="ok",
                latency_ms=300,
                prompt_chars=100,
                response_chars=2,
                prompt_tokens=1000,
                completion_tokens=200,
                total_tokens=1200,
                context_json={
                    "system_evaluation_id": "eval-a",
                    "model_route": "pro_quality",
                },
            ),
        ]
    )
    db_session.commit()
    reporter = AgentSystemEvaluationReporter(base_path=Path.cwd(), experiment_id="eval-a")

    report = reporter.usage_report(db_session, start_log_id=0)

    assert report["call_count"] == 2
    assert report["cache_hit_prompt_tokens"] == 700
    assert report["cache_miss_prompt_tokens"] == 300
    assert report["latency_ms"]["p50"] == 200.0
    assert report["cost_cny"]["lower_bound"] < report["cost_cny"]["upper_bound"]
    assert report["by_route"]["flash_economy"]["call_count"] == 1
    assert report["by_trace_group"]["resume_parser.parse"]["call_count"] == 1
    assert report["call_success_rate"] == 1.0


def test_system_reporter_calculates_empirical_pass_power_k():
    reporter = AgentSystemEvaluationReporter(base_path=Path.cwd(), experiment_id="eval-b")

    report = reporter.reliability_report(
        [
            [{"name": "case-a", "case_passed": True}, {"name": "case-b", "case_passed": True}],
            [{"name": "case-a", "case_passed": True}, {"name": "case-b", "case_passed": False}],
        ]
    )

    assert report["repetitions"] == 2
    assert report["pass_at_1"] == 0.75
    assert report["pass_power_k"] == 0.5


def test_system_reporter_release_gate_does_not_average_failed_suite(db_session):
    passed = EvaluationRun(
        name="passed",
        summary_json={"case_count": 10, "release_gate": {"passed": True}},
        case_results_json=[],
    )
    failed = EvaluationRun(
        name="failed",
        summary_json={"case_count": 10, "release_gate": {"passed": False}},
        case_results_json=[],
    )
    db_session.add_all([passed, failed])
    db_session.commit()
    reporter = AgentSystemEvaluationReporter(base_path=Path.cwd(), experiment_id="eval-c")

    summary = reporter.build_summary(
        mode="deterministic",
        suites={"passed": passed, "failed": failed},
        suite_errors={},
        usage={"cost_cny": {}, "total_tokens": 0},
        wall_time_ms=10,
        required_suites=["passed", "failed"],
    )

    assert summary["release_gate"]["passed"] is False
    assert summary["release_gate"]["checks"][1]["passed"] is False


def test_system_reporter_accepts_an_explicit_required_suite_slice(db_session):
    passed = EvaluationRun(
        name="pdf_extraction_bad_case_evaluation",
        summary_json={"release_gate": {"passed": True}, "pass_rate": 1.0},
        case_results_json=[],
    )
    db_session.add(passed)
    db_session.commit()

    summary = AgentSystemEvaluationReporter(
        base_path=Path.cwd(),
        experiment_id="eval-slice",
    ).build_summary(
        mode="deterministic",
        suites={"pdf_extraction_bad_cases": passed},
        suite_errors={},
        usage={"total_tokens": 0, "call_count": 0},
        wall_time_ms=10,
        required_suites=["pdf_extraction_bad_cases"],
    )

    assert summary["release_gate"]["passed"] is True


def test_system_reporter_gates_full_mode_on_empirical_reliability(db_session):
    passed = EvaluationRun(
        name="passed",
        summary_json={"release_gate": {"passed": True}},
        case_results_json=[],
    )
    db_session.add(passed)
    db_session.commit()
    reporter = AgentSystemEvaluationReporter(base_path=Path.cwd(), experiment_id="eval-r")

    summary = reporter.build_summary(
        mode="full",
        suites={"passed": passed},
        suite_errors={},
        usage={"cost_cny": {}, "total_tokens": 0},
        wall_time_ms=10,
        reliability={"repetitions": 2, "pass_power_k": 0.5},
        required_suites=["passed"],
    )

    assert summary["release_gate"]["passed"] is False
    assert summary["release_gate"]["checks"][-1]["suite"] == "workflow_reliability_pass_power_k"


def test_system_reporter_dataset_manifest_records_current_scale():
    reporter = AgentSystemEvaluationReporter(base_path=Path.cwd(), experiment_id="eval-d")

    manifest = reporter.dataset_manifest()

    assert manifest["pdf_chunk_cases.json"]["case_count"] >= 90
    assert manifest["pdf_extraction_bad_cases.json"]["case_count"] >= 20
    assert manifest["follow_up_directive_bad_cases.json"]["case_count"] >= 20
    assert manifest["rag_cases.json"]["case_count"] >= 180
    assert manifest["prompt_injection_cases.json"]["case_count"] >= 70
