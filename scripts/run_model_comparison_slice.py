from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any


def _bootstrap_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one controlled DeepSeek model comparison slice.")
    parser.add_argument("--model", required=True, choices=["deepseek-v4-pro", "deepseek-v4-flash"])
    parser.add_argument("--mode", required=True, choices=["canary", "core", "interview"])
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--output", default=None)
    parser.add_argument("--token-budget", type=int, default=50000)
    parser.add_argument("--allow-quality-failures", action="store_true")
    return parser.parse_args()


ARGS = _bootstrap_args()
os.environ["LLM_MODEL"] = ARGS.model
os.environ["LLM_BASE_URL"] = ARGS.base_url
os.environ["LLM_THINKING_MODE"] = "disabled"
os.environ["LLM_FALLBACK_ENABLED"] = "false"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from sqlalchemy import func, select

from app.core.database import SessionLocal, init_db
from app.core.llm import llm_trace_context
from app.models.entities import Job, LLMCallLog, Profile, ResumeVersion
from app.services.application_service import ApplicationService
from app.services.evaluation_service import EvaluationService


CANARY_WORKFLOW_CASES = [18]
CORE_PLAN_CASES = [0, 5, 10, 19]
CORE_JD_CASES = [1, 15, 21, 29]
CORE_WORKFLOW_CASES = [19, 21, 23]
INTERVIEW_CASES = [0]


def _gate_passed(summary: dict[str, Any]) -> bool:
    gate = summary.get("release_gate") or {}
    return bool(gate.get("passed"))


def _tokens_used(db, benchmark_id: str) -> int:
    rows = db.scalars(select(LLMCallLog).where(LLMCallLog.model == ARGS.model)).all()
    return sum(
        int(row.total_tokens or 0)
        for row in rows
        if (row.context_json or {}).get("benchmark_run_id") == benchmark_id
    )


def _assert_budget(db, benchmark_id: str, *, next_stage: str) -> None:
    used = _tokens_used(db, benchmark_id)
    if used >= ARGS.token_budget:
        raise RuntimeError(
            f"benchmark token budget exhausted before {next_stage}: "
            f"used={used}, budget={ARGS.token_budget}"
        )


def _compact_plan_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": case.get("name"),
        "passed": case.get("case_passed"),
        "intent": case.get("actual_intent"),
        "intent_passed": case.get("intent_passed"),
        "action_precision": case.get("action_precision"),
        "action_recall": case.get("action_recall"),
        "forbidden_actions_hit": case.get("forbidden_actions_hit"),
        "needs_profile_passed": case.get("needs_profile_passed"),
        "needs_job_passed": case.get("needs_job_passed"),
        "error": case.get("error"),
    }


def _compact_jd_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": case.get("name"),
        "passed": case.get("case_passed"),
        "parser_mode": case.get("parser_mode"),
        "required_skill_recall": case.get("required_skill_recall"),
        "required_skill_precision": case.get("required_skill_precision"),
        "required_skill_f1": case.get("required_skill_f1"),
        "grounding_quality_gate_passed": case.get("grounding_quality_gate_passed"),
        "unsupported_keyword_expansion_count": case.get("unsupported_keyword_expansion_count"),
        "parsed_required_skills": case.get("parsed_required_skills"),
        "missing_required_skills": case.get("missing_required_skills"),
        "absent_required_skill_violations": case.get("absent_required_skill_violations"),
        "failed_checks": case.get("failed_checks"),
        "error": case.get("error"),
    }


def _compact_workflow_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": case.get("name"),
        "difficulty": case.get("difficulty"),
        "passed": case.get("case_passed"),
        "status": case.get("status"),
        "failed_stage": case.get("failed_stage"),
        "expected_fit_label": case.get("expected_fit_label"),
        "predicted_fit_label": case.get("predicted_fit_label"),
        "predicted_fit_score": case.get("predicted_fit_score"),
        "profile_grounding": case.get("profile_field_grounding_rate"),
        "jd_grounding": case.get("jd_statement_grounding_rate"),
        "top5_evidence_hit_rate": case.get("matcher_top5_evidence_hit_rate"),
        "fit_explanation_passed": case.get("fit_explanation_passed"),
        "tailor_passed": case.get("tailor_passed"),
        "tailor_semantic_grounding_rate": case.get("tailor_semantic_grounding_rate"),
        "react_repair": case.get("tailor_react_repair"),
        "profile_id": case.get("profile_id"),
        "job_id": case.get("job_id"),
        "resume_version_id": case.get("resume_version_id"),
        "latency_ms": case.get("latency_ms"),
        "error": case.get("error"),
    }


def _compact_interview_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": case.get("name"),
        "passed": case.get("case_passed"),
        "question_count": case.get("question_count"),
        "question_quality_score": case.get("question_quality_score"),
        "required_skill_coverage_rate": (case.get("coverage") or {}).get("required_skill_coverage_rate"),
        "question_id_passed": case.get("question_id_passed"),
        "source_perspective_passed": case.get("source_perspective_passed"),
        "preparation_angle_passed": case.get("preparation_angle_passed"),
        "question_quality_passed": case.get("question_quality_passed"),
        "keyword_hit_rate": case.get("keyword_hit_rate"),
        "agentic_rag": case.get("agentic_rag"),
    }


async def _create_application_from_workflow(db, workflow_cases: list[dict[str, Any]]) -> dict[str, Any]:
    source = next(
        (
            case
            for case in workflow_cases
            if case.get("case_passed")
            and case.get("resume_version_id")
            and case.get("predicted_fit_label") in {"strong_fit", "partial_fit"}
        ),
        None,
    )
    if source is None:
        return {"passed": False, "skipped": True, "reason": "no passed strong/partial tailored workflow case"}
    profile = db.get(Profile, int(source["profile_id"]))
    job = db.get(Job, int(source["job_id"]))
    version = db.get(ResumeVersion, int(source["resume_version_id"]))
    if not profile or not job or not version:
        return {"passed": False, "skipped": True, "reason": "workflow artifacts missing"}
    try:
        application = await ApplicationService().create_quick_apply_packet(
            db,
            profile=profile,
            job=job,
            resume_version=version,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "passed": False,
            "skipped": False,
            "error": f"{type(exc).__name__}: {exc}",
            "source_case": source.get("name"),
        }
    validation = (application.automation_result_json or {}).get("packet_validation") or {}
    return {
        "passed": validation.get("passed") is True and application.status == "ready",
        "skipped": False,
        "application_id": application.id,
        "status": application.status,
        "risk_level": validation.get("risk_level"),
        "semantic_grounding_rate": (validation.get("semantic_claim_grounding") or {}).get("grounding_rate"),
        "issue_codes": [item.get("code") for item in validation.get("issues") or []],
        "warning_codes": [item.get("code") for item in validation.get("warnings") or []],
    }


async def _run_canary(service: EvaluationService, db, benchmark_id: str) -> dict[str, Any]:
    trace_path = ROOT / "data" / "runtime" / f"model_benchmark_{benchmark_id}_canary.jsonl"
    with llm_trace_context(
        benchmark_run_id=benchmark_id,
        benchmark_model=ARGS.model,
        benchmark_mode="canary",
    ):
        workflow = await service.run_llm_workflow_evaluation(
            db,
            case_indexes=CANARY_WORKFLOW_CASES,
            trace_path=trace_path,
        )
        _assert_budget(db, benchmark_id, next_stage="application")
        application = await _create_application_from_workflow(db, workflow.case_results_json)
    return {
        "workflow": {
            "evaluation_run_id": workflow.id,
            "summary": workflow.summary_json,
            "cases": [_compact_workflow_case(case) for case in workflow.case_results_json],
        },
        "application": application,
        "passed": _gate_passed(workflow.summary_json) and application.get("passed") is True,
    }


async def _run_core(service: EvaluationService, db, benchmark_id: str) -> dict[str, Any]:
    trace_path = ROOT / "data" / "runtime" / f"model_benchmark_{benchmark_id}_core.jsonl"
    with llm_trace_context(benchmark_run_id=benchmark_id, benchmark_model=ARGS.model, benchmark_mode="core"):
        plan = await service.run_natural_language_plan_evaluation(db, case_indexes=CORE_PLAN_CASES)
        _assert_budget(db, benchmark_id, next_stage="jd_parser")
        jd = await service.run_jd_parser_evaluation(db, case_indexes=CORE_JD_CASES)
        _assert_budget(db, benchmark_id, next_stage="workflow")
        workflow = await service.run_llm_workflow_evaluation(
            db,
            case_indexes=CORE_WORKFLOW_CASES,
            trace_path=trace_path,
        )
        _assert_budget(db, benchmark_id, next_stage="application")
        application = await _create_application_from_workflow(db, workflow.case_results_json)
    suite_passed = (
        _gate_passed(plan.summary_json)
        and _gate_passed(jd.summary_json)
        and _gate_passed(workflow.summary_json)
        and application.get("passed") is True
    )
    return {
        "natural_language_plan": {
            "evaluation_run_id": plan.id,
            "summary": plan.summary_json,
            "cases": [_compact_plan_case(case) for case in plan.case_results_json],
        },
        "jd_parser": {
            "evaluation_run_id": jd.id,
            "summary": jd.summary_json,
            "cases": [_compact_jd_case(case) for case in jd.case_results_json],
        },
        "workflow": {
            "evaluation_run_id": workflow.id,
            "summary": workflow.summary_json,
            "cases": [_compact_workflow_case(case) for case in workflow.case_results_json],
        },
        "application": application,
        "passed": suite_passed,
    }


def _run_interview(service: EvaluationService, db, benchmark_id: str) -> dict[str, Any]:
    with llm_trace_context(
        benchmark_run_id=benchmark_id,
        benchmark_model=ARGS.model,
        benchmark_mode="interview",
    ):
        interview = service.run_interview_prep_evaluation(db, case_indexes=INTERVIEW_CASES)
    return {
        "interview": {
            "evaluation_run_id": interview.id,
            "summary": interview.summary_json,
            "cases": [_compact_interview_case(case) for case in interview.case_results_json],
        },
        "passed": _gate_passed(interview.summary_json),
    }


def _usage_report(db, *, start_log_id: int, benchmark_id: str) -> dict[str, Any]:
    logs = list(
        db.scalars(
            select(LLMCallLog)
            .where(LLMCallLog.id > start_log_id, LLMCallLog.model == ARGS.model)
            .order_by(LLMCallLog.id)
        )
    )
    logs = [row for row in logs if (row.context_json or {}).get("benchmark_run_id") == benchmark_id]
    by_trace: dict[str, dict[str, Any]] = {}
    for row in logs:
        item = by_trace.setdefault(
            row.trace_name,
            {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "latency_ms": 0,
                "statuses": {},
            },
        )
        item["calls"] += 1
        item["prompt_tokens"] += int(row.prompt_tokens or 0)
        item["completion_tokens"] += int(row.completion_tokens or 0)
        item["total_tokens"] += int(row.total_tokens or 0)
        item["latency_ms"] += int(row.latency_ms or 0)
        item["statuses"][row.status] = item["statuses"].get(row.status, 0) + 1
    return {
        "first_log_id": logs[0].id if logs else None,
        "last_log_id": logs[-1].id if logs else None,
        "call_count": len(logs),
        "completed_call_count": sum(1 for row in logs if row.status == "completed"),
        "failed_call_count": sum(1 for row in logs if row.status != "completed"),
        "prompt_tokens": sum(int(row.prompt_tokens or 0) for row in logs),
        "completion_tokens": sum(int(row.completion_tokens or 0) for row in logs),
        "total_tokens": sum(int(row.total_tokens or 0) for row in logs),
        "provider_latency_ms": sum(int(row.latency_ms or 0) for row in logs),
        "retry_call_count": sum("retry" in row.trace_name.lower() for row in logs),
        "repair_call_count": sum("repair" in row.trace_name.lower() for row in logs),
        "trace_breakdown": by_trace,
        "errors": [
            {"id": row.id, "trace_name": row.trace_name, "status": row.status, "error": row.error_message}
            for row in logs
            if row.status != "completed"
        ],
    }


def main() -> int:
    if not os.getenv("LLM_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("LLM_API_KEY must be provided through the process environment.")
    init_db()
    benchmark_id = f"{ARGS.model}-{ARGS.mode}-{uuid.uuid4().hex[:8]}"
    started = time.perf_counter()
    db = SessionLocal()
    start_log_id = int(db.scalar(select(func.max(LLMCallLog.id))) or 0)
    result: dict[str, Any] = {
        "benchmark_run_id": benchmark_id,
        "model": ARGS.model,
        "mode": ARGS.mode,
        "thinking_mode": "disabled",
        "fallback_enabled": False,
        "token_budget": ARGS.token_budget,
        "status": "running",
    }
    exit_code = 1
    try:
        service = EvaluationService()
        if ARGS.mode == "canary":
            suites = asyncio.run(_run_canary(service, db, benchmark_id))
        elif ARGS.mode == "core":
            suites = asyncio.run(_run_core(service, db, benchmark_id))
        else:
            suites = _run_interview(service, db, benchmark_id)
        result["suites"] = suites
        result["status"] = "completed" if suites.get("passed") else "completed_with_quality_failures"
        exit_code = 0 if suites.get("passed") or ARGS.allow_quality_failures else 2
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        result["status"] = "failed"
        result["error"] = f"{exc.__class__.__name__}: {str(exc).strip() or repr(exc)}"
        exit_code = 1
    finally:
        result["usage"] = _usage_report(db, start_log_id=start_log_id, benchmark_id=benchmark_id)
        result["wall_time_ms"] = int((time.perf_counter() - started) * 1000)
        db.close()

    safe_model_name = re.sub(r"[^a-z0-9]+", "_", ARGS.model)
    output_path = (
        Path(ARGS.output)
        if ARGS.output
        else ROOT / "data" / "runtime" / f"model_benchmark_{safe_model_name}_{ARGS.mode}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**result, "output_path": str(output_path)}, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
