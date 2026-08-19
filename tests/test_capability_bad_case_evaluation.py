from __future__ import annotations

import asyncio

from app.services.capability_bad_case_evaluation import CapabilityBadCaseEvaluationService


def test_pdf_extraction_bad_case_evaluation_is_quantitative(db_session):
    run = CapabilityBadCaseEvaluationService().run_pdf_extraction(db_session)
    summary = run.summary_json

    assert summary["case_count"] >= 20
    assert 0 <= summary["pass_rate"] <= 1
    assert 0 <= summary["critical_case_pass_rate"] <= 1
    assert 0 <= summary["bridge_precision"] <= 1
    assert 0 <= summary["bridge_recall"] <= 1
    assert {"input_validation", "ocr_route", "layout", "cross_page_bridge"} <= set(
        summary["category_breakdown"]
    )
    assert len(summary["release_gate"]["checks"]) == 5
    assert summary["release_gate"]["passed"] is True
    assert all("observed" in row and "errors" in row for row in run.case_results_json)


def test_follow_up_directive_bad_case_evaluation_is_quantitative(db_session):
    run = asyncio.run(CapabilityBadCaseEvaluationService().run_follow_up_directives(db_session))
    summary = run.summary_json

    assert summary["case_count"] >= 20
    assert 0 <= summary["pass_rate"] <= 1
    assert 0 <= summary["idempotency_safety_rate"] <= 1
    assert 0 <= summary["lineage_integrity_rate"] <= 1
    assert 0 <= summary["context_minimization_rate"] <= 1
    assert {"concurrency_guard", "idempotency_safety", "failure_audit"} <= set(
        summary["category_breakdown"]
    )
    assert len(summary["release_gate"]["checks"]) == 7
    assert summary["release_gate"]["passed"] is True
    assert all("scenario" in row and "errors" in row for row in run.case_results_json)
