from datetime import datetime, timezone

from app.models.entities import AgentArtifact, AgentRun, HttpRequestMetric
from app.services.slo_service import SLOService


def test_slo_report_keeps_synthetic_traffic_separate_and_meets_targets(db_session):
    for index in range(60):
        db_session.add(
            HttpRequestMetric(
                method="GET",
                route_template="/jobs",
                status_code=200,
                latency_ms=20 + index,
                traffic_class="synthetic",
            )
        )
    for index in range(20):
        run = AgentRun(
            task_type="find_jobs_for_profile",
            status="completed",
            input_json={"_traffic_class": "synthetic"},
            output_json={},
            latency_ms=1000 + index,
        )
        db_session.add(run)
        db_session.flush()
        db_session.add(
            AgentArtifact(
                run_id=run.id,
                artifact_type="completion_verification",
                artifact_json={"passed": True},
            )
        )
    db_session.add(
        HttpRequestMetric(
            method="GET",
            route_template="/jobs",
            status_code=500,
            latency_ms=9999,
            traffic_class="real",
        )
    )
    db_session.commit()

    report = SLOService().report(
        db_session,
        window_days=7,
        traffic_class="synthetic",
        now=datetime.now(timezone.utc),
    )

    assert report["status"] == "met"
    assert report["sample_counts"] == {"http_user_api": 60, "agent_runs": 20}
    assert all(item["status"] == "met" for item in report["objectives"])
    availability = next(item for item in report["objectives"] if item["name"] == "user_api_availability")
    assert availability["value"] == 1.0
    assert availability["wilson_95_lower_bound"] < availability["value"]


def test_slo_report_is_partial_instead_of_claiming_success_when_agent_samples_are_missing(db_session):
    for _ in range(50):
        db_session.add(
            HttpRequestMetric(
                method="GET",
                route_template="/profiles",
                status_code=200,
                latency_ms=10,
                traffic_class="synthetic",
            )
        )
    db_session.commit()

    report = SLOService().report(db_session, window_days=30, traffic_class="synthetic")

    assert report["status"] == "partial"
    terminal = next(item for item in report["objectives"] if item["name"] == "agent_valid_terminal_rate")
    assert terminal["status"] == "insufficient_data"


def test_slo_completion_integrity_consumes_error_budget(db_session):
    for index in range(20):
        run = AgentRun(
            task_type="find_jobs_for_profile",
            status="completed",
            input_json={"_traffic_class": "synthetic"},
            output_json={},
            latency_ms=100,
        )
        db_session.add(run)
        db_session.flush()
        if index:
            db_session.add(
                AgentArtifact(
                    run_id=run.id,
                    artifact_type="completion_verification",
                    artifact_json={"passed": True},
                )
            )
    db_session.commit()

    report = SLOService().report(db_session, window_days=7, traffic_class="synthetic")
    integrity = next(item for item in report["objectives"] if item["name"] == "completion_integrity_rate")

    assert integrity["status"] == "breached"
    assert integrity["value"] == 0.95
    assert integrity["error_budget"]["remaining_bad_samples"] == -1


def test_multilingual_rag_dataset_has_paired_language_and_hard_negative_coverage():
    import json
    from pathlib import Path

    payload = json.loads(
        (Path(__file__).resolve().parents[1] / "evals" / "rag_multilingual_calibration.json").read_text(
            encoding="utf-8"
        )
    )
    cases = payload["cases"]
    pairs = {case["language_pair"] for case in cases}

    assert len(cases) >= 140
    assert len({case["concept_id"] for case in cases}) >= 24
    assert pairs == {"zh_zh", "en_en", "zh_en", "en_zh", "mixed_zh", "mixed_en"}
    assert all(sum(chunk["expected"] for chunk in case["evidence_chunks"]) == 1 for case in cases)
    assert all(any(chunk["noise_profile"] == "same_topic_wrong_evidence" for chunk in case["evidence_chunks"]) for case in cases)
