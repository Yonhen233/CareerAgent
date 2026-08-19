from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 CareerAgent 分层系统评测。")
    parser.add_argument("--mode", choices=["deterministic", "full"], default="deterministic")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--default-model", default="deepseek-v4-flash")
    parser.add_argument("--token-budget", type=int, default=350000)
    parser.add_argument("--interview-case-limit", type=int, default=2)
    parser.add_argument("--reliability-repetitions", type=int, default=2)
    parser.add_argument("--include-live-sources", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--resume-output", default=None)
    parser.add_argument("--only", action="append", default=[])
    return parser.parse_args()


def configure_environment(args: argparse.Namespace) -> None:
    os.environ["LLM_BASE_URL"] = args.base_url
    os.environ["LLM_MODEL"] = args.default_model
    os.environ["LLM_ROUTING_ENABLED"] = "true"
    os.environ["LLM_THINKING_MODE"] = "disabled"
    os.environ["LLM_FALLBACK_ENABLED"] = "false"
    os.environ.setdefault("EMBEDDING_PROVIDER", "sentence_transformers")
    os.environ.setdefault("EMBEDDING_PROVIDER_FALLBACK", "error")
    os.environ.setdefault("RERANKER_ENABLED", "true")
    os.environ.setdefault("RERANKER_PROVIDER", "cross_encoder")
    os.environ.setdefault("RERANKER_PROVIDER_FALLBACK", "error")


async def run(args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from app.core.database import SessionLocal, init_db
    from app.core.llm import llm_trace_context
    from app.models.entities import EvaluationRun, LLMCallLog
    from app.services.agent_system_evaluation import AgentSystemEvaluationReporter
    from app.services.capability_bad_case_evaluation import CapabilityBadCaseEvaluationService
    from app.services.evaluation_service import EvaluationService
    from app.services.interview_claim_evaluation import InterviewClaimVerifierEvaluationService

    if args.mode == "full" and not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")):
        raise RuntimeError("full 模式需要通过进程环境提供 LLM_API_KEY。")
    if args.token_budget <= 0:
        raise ValueError("token-budget 必须大于 0。")
    if args.interview_case_limit < 0 or args.interview_case_limit > 9:
        raise ValueError("interview-case-limit 必须在 0 到 9 之间。")
    if args.reliability_repetitions < 1 or args.reliability_repetitions > 3:
        raise ValueError("reliability-repetitions 必须在 1 到 3 之间。")

    init_db()
    db = SessionLocal()
    prior: dict[str, Any] = {}
    if args.resume_output:
        resume_path = Path(args.resume_output)
        prior = json.loads(resume_path.read_text(encoding="utf-8"))
        experiment_id = str(prior["experiment_id"])
        args.mode = str(prior.get("mode") or args.mode)
    else:
        experiment_id = f"agent-system-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    invocation_id = f"invocation-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    reporter = AgentSystemEvaluationReporter(base_path=ROOT, experiment_id=experiment_id)
    start_log_id = 0 if prior else int(db.scalar(select(func.max(LLMCallLog.id))) or 0)
    started = time.perf_counter()
    suites: dict[str, EvaluationRun] = {}
    for name, payload in (prior.get("suites") or {}).items():
        run_id = payload.get("evaluation_run_id") if isinstance(payload, dict) else None
        prior_run = db.get(EvaluationRun, run_id) if run_id else None
        if prior_run is not None:
            suites[name] = prior_run
    suite_errors: dict[str, str] = dict(prior.get("suite_errors") or {})
    suite_wall_time_ms: dict[str, int] = dict(prior.get("suite_wall_time_ms") or {})
    reliability_rows: list[list[dict[str, Any]]] = []
    output_path = (
        Path(args.resume_output or args.output)
        if args.resume_output or args.output
        else ROOT / "data" / "runtime" / f"{experiment_id}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected_suites = {item.strip() for raw in args.only for item in raw.split(",") if item.strip()}
    prior_wall_time_ms = int((prior.get("performance") or {}).get("wall_time_ms") or 0)

    def tokens_used() -> int:
        rows = list(db.scalars(select(LLMCallLog).where(LLMCallLog.id > start_log_id)))
        return sum(
            int(row.total_tokens or 0)
            for row in rows
            if (row.context_json or {}).get("system_evaluation_id") == experiment_id
        )

    def assert_budget(next_suite: str) -> None:
        used = tokens_used()
        if used >= args.token_budget:
            raise RuntimeError(
                f"真实 LLM 评测预算已耗尽，停止在 {next_suite} 之前：used={used}, budget={args.token_budget}。"
            )

    def write_progress(status: str) -> None:
        usage = reporter.usage_report(db, start_log_id=start_log_id)
        payload = {
            "experiment_id": experiment_id,
            "invocation_id": invocation_id,
            "mode": args.mode,
            "status": status,
            "git_revision": git_revision(),
            "token_budget": args.token_budget,
            "tokens_used": usage["total_tokens"],
            "suite_wall_time_ms": suite_wall_time_ms,
            "suites": {name: reporter.compact_suite(run) for name, run in suites.items()},
            "suite_errors": suite_errors,
            "usage": usage,
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    async def execute(
        name: str,
        callback: Callable[[], Any],
        *,
        uses_llm: bool = False,
        run_in_thread: bool = False,
    ) -> None:
        if selected_suites and name not in selected_suites:
            return
        if uses_llm:
            assert_budget(name)
        suite_started = time.perf_counter()
        try:
            if run_in_thread:
                run_result = await asyncio.to_thread(callback)
            else:
                value = callback()
                run_result = await value if inspect.isawaitable(value) else value
            suites[name] = run_result
            suite_errors.pop(name, None)
            db.expire_all()
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            suite_errors[name] = f"{exc.__class__.__name__}: {str(exc).strip() or repr(exc)}"
        finally:
            suite_wall_time_ms[name] = int((time.perf_counter() - suite_started) * 1000)
            write_progress(f"running:{name}")

    service = EvaluationService()
    capability_bad_cases = CapabilityBadCaseEvaluationService()
    with llm_trace_context(
        system_evaluation_id=experiment_id,
        evaluation_invocation_id=invocation_id,
        evaluation_mode=args.mode,
    ):
        await execute("pdf_chunk", lambda: service.run_pdf_chunk_strategy_evaluation(db))
        await execute("pdf_extraction_bad_cases", lambda: capability_bad_cases.run_pdf_extraction(db))
        await execute(
            "follow_up_directive_bad_cases",
            lambda: capability_bad_cases.run_follow_up_directives(db),
        )
        await execute("rag", lambda: service.run_rag_strategy_evaluation(db))
        await execute("job_relevance", lambda: service.run_job_relevance_evaluation(db))
        await execute("application_packet", lambda: service.run_application_packet_evaluation(db))
        await execute("prompt_injection", lambda: service.run_prompt_injection_evaluation(db))

        if args.mode == "full":
            await execute(
                "natural_language_plan",
                lambda: service.run_natural_language_plan_evaluation(db),
                uses_llm=True,
            )
            await execute("jd_parser", lambda: service.run_jd_parser_evaluation(db), uses_llm=True)
            await execute(
                "llm_workflow",
                lambda: service.run_llm_workflow_evaluation(
                    db,
                    trace_path=ROOT / "data" / "runtime" / f"{experiment_id}-workflow.jsonl",
                ),
                uses_llm=True,
            )
            workflow = suites.get("llm_workflow")
            reliability_indexes = [18, 21, 23]
            if workflow is not None and (not selected_suites or "llm_workflow" in selected_suites):
                selected_names = {
                    json.loads((ROOT / "evals" / "llm_workflow_cases.json").read_text(encoding="utf-8"))[index][
                        "name"
                    ]
                    for index in reliability_indexes
                }
                reliability_rows.append(
                    [case for case in workflow.case_results_json if case.get("name") in selected_names]
                )
            for repetition in range(2, args.reliability_repetitions + 1):
                suite_name = f"llm_workflow_reliability_{repetition}"
                await execute(
                    suite_name,
                    lambda repetition=repetition: service.run_llm_workflow_evaluation(
                        db,
                        case_indexes=reliability_indexes,
                        trace_path=ROOT
                        / "data"
                        / "runtime"
                        / f"{experiment_id}-reliability-{repetition}.jsonl",
                    ),
                    uses_llm=True,
                )
                if suite_name in suites and (not selected_suites or suite_name in selected_suites):
                    reliability_rows.append(list(suites[suite_name].case_results_json or []))
            await execute("agent_full_flow", lambda: service.run_agent_full_flow_evaluation(db), uses_llm=True)
            await execute(
                "interview_claim_verifier",
                lambda: InterviewClaimVerifierEvaluationService().run(db),
                uses_llm=True,
            )
            if args.interview_case_limit:
                def run_interview_prep() -> EvaluationRun:
                    isolated_db = SessionLocal()
                    try:
                        return EvaluationService().run_interview_prep_evaluation(
                            isolated_db,
                            case_limit=args.interview_case_limit,
                        )
                    finally:
                        isolated_db.close()

                await execute(
                    "interview_prep",
                    run_interview_prep,
                    uses_llm=True,
                    run_in_thread=True,
                )

        if args.include_live_sources:
            await execute(
                "real_job_source_smoke",
                lambda: service.run_real_job_source_smoke(
                    db,
                    query="Agent 开发实习生",
                    location=None,
                    limit=8,
                    sources=None,
                ),
            )

    usage = reporter.usage_report(db, start_log_id=start_log_id)
    reliability = (
        reporter.reliability_report(reliability_rows)
        if reliability_rows
        else prior.get("reliability") or reporter.reliability_report([])
    )
    deterministic_required = [
        "pdf_chunk",
        "pdf_extraction_bad_cases",
        "follow_up_directive_bad_cases",
        "rag",
        "job_relevance",
        "application_packet",
        "prompt_injection",
    ]
    full_required = [
        *deterministic_required,
        "natural_language_plan",
        "jd_parser",
        "llm_workflow",
        "agent_full_flow",
        "interview_claim_verifier",
    ]
    if args.mode == "full" and args.interview_case_limit:
        full_required.append("interview_prep")
    required_suites = full_required if args.mode == "full" else deterministic_required
    if selected_suites:
        required_suites = [suite for suite in required_suites if suite in selected_suites]
    summary = reporter.build_summary(
        mode=args.mode,
        suites=suites,
        suite_errors=suite_errors,
        usage=usage,
        wall_time_ms=prior_wall_time_ms + int((time.perf_counter() - started) * 1000),
        reliability=reliability,
        required_suites=required_suites,
    )
    summary["git_revision"] = git_revision()
    summary["token_budget"] = args.token_budget
    summary["suite_wall_time_ms"] = suite_wall_time_ms
    prior_run_id = prior.get("evaluation_run_id") if prior else None
    system_run = db.get(EvaluationRun, prior_run_id) if prior_run_id else None
    if system_run is None:
        system_run = EvaluationRun(name="agent_system_evaluation", summary_json={}, case_results_json=[])
    system_run.summary_json = summary
    system_run.case_results_json = [
        {"suite": name, **reporter.compact_suite(run)} for name, run in suites.items()
    ]
    db.add(system_run)
    db.commit()
    db.refresh(system_run)
    summary["evaluation_run_id"] = system_run.id
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    db.close()
    print(
        json.dumps(
            {
                "experiment_id": experiment_id,
                "invocation_id": invocation_id,
                "evaluation_run_id": system_run.id,
                "release_gate_passed": summary["release_gate"]["passed"],
                "suite_errors": suite_errors,
                "total_tokens": usage["total_tokens"],
                "cost_cny": usage["cost_cny"],
                "wall_time_ms": summary["performance"]["wall_time_ms"],
                "output_path": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if summary["release_gate"]["passed"] else 2


def git_revision() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    args = parse_args()
    configure_environment(args)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
