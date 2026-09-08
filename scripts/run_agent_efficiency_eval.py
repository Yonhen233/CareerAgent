from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 CareerAgent Agent 效率与可靠性评测。")
    parser.add_argument("--mode", choices=["deterministic", "full"], default="deterministic")
    parser.add_argument("--repetitions", type=int, default=3, help="真实链路重复次数，用于 Pass^K。")
    parser.add_argument("--case-limit", type=int, default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--default-model", default=None)
    parser.add_argument("--token-budget", type=int, default=60000)
    parser.add_argument("--cache-iterations", type=int, default=10)
    parser.add_argument("--cache-concurrency", type=int, default=4)
    parser.add_argument("--interview-case-limit", type=int, default=0)
    parser.add_argument("--output", default=None)
    parser.add_argument("--trace-output", default=None)
    parser.add_argument("--reuse-run-ids", default=None, help="复用已完成的 EvaluationRun，逗号分隔。")
    parser.add_argument("--reuse-interview-run-ids", default=None, help="复用面试准备 EvaluationRun，逗号分隔。")
    parser.add_argument("--experiment-id", default=None)
    return parser.parse_args()


def configure_environment(args: argparse.Namespace) -> None:
    if args.base_url:
        os.environ["LLM_BASE_URL"] = args.base_url
    if args.default_model:
        os.environ["LLM_MODEL"] = args.default_model
    if args.mode == "full":
        os.environ.setdefault("LLM_ROUTING_ENABLED", "true")
        os.environ.setdefault("LLM_THINKING_MODE", "disabled")
        os.environ.setdefault("LLM_FALLBACK_ENABLED", "false")
        os.environ.setdefault("EMBEDDING_PROVIDER", "sentence_transformers")
        os.environ.setdefault("EMBEDDING_PROVIDER_FALLBACK", "error")
        os.environ.setdefault("RERANKER_ENABLED", "true")
        os.environ.setdefault("RERANKER_PROVIDER", "cross_encoder")
        os.environ.setdefault("RERANKER_PROVIDER_FALLBACK", "error")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(args: argparse.Namespace) -> int:
    if args.repetitions < 1 or args.repetitions > 10:
        raise ValueError("repetitions 必须在 1 到 10 之间。")
    if args.case_limit is not None and args.case_limit < 1:
        raise ValueError("case-limit 必须大于 0。")
    if args.token_budget <= 0:
        raise ValueError("token-budget 必须大于 0。")
    configure_environment(args)

    from sqlalchemy import func, select

    from app.core.database import SessionLocal, init_db
    from app.core.llm import llm_trace_context
    from app.models.entities import EvaluationRun, LLMCallLog
    from app.services.agent_efficiency_evaluator import AgentEfficiencyEvaluator
    from app.services.evaluation_service import EvaluationService
    from app.services.rerank_result_cache import cache_metrics_snapshot

    init_db()
    db = SessionLocal()
    experiment_id = args.experiment_id or f"agent-efficiency-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    output_path = Path(args.output) if args.output else ROOT / "evals" / "results" / f"{experiment_id}.json"
    trace_path = (
        Path(args.trace_output)
        if args.trace_output
        else ROOT / "evals" / "results" / f"{experiment_id}.jsonl"
    )
    evaluator = AgentEfficiencyEvaluator(base_path=ROOT)
    start_log_id = 0 if args.reuse_run_ids else int(db.scalar(select(func.max(LLMCallLog.id))) or 0)
    full_runs: list[EvaluationRun] = []
    interview_runs: list[EvaluationRun] = []
    errors: list[str] = []
    started = time.perf_counter()

    fault_report = evaluator.run_fault_injection()
    cache_report = evaluator.run_cache_ab(
        iterations=args.cache_iterations,
        concurrent_calls=args.cache_concurrency,
    )
    cache_metrics_before_real = cache_metrics_snapshot()

    reused_ids = [int(item.strip()) for item in (args.reuse_run_ids or "").split(",") if item.strip()]
    if reused_ids:
        full_runs = [db.get(EvaluationRun, run_id) for run_id in reused_ids]
        missing = [str(run_id) for run_id, run in zip(reused_ids, full_runs, strict=False) if run is None]
        if missing:
            raise RuntimeError(f"找不到要复用的 EvaluationRun: {', '.join(missing)}")
        full_runs = [run for run in full_runs if run is not None]
    elif args.mode == "full":
        if not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")):
            raise RuntimeError("full 模式需要通过进程环境提供 LLM_API_KEY。")
        service = EvaluationService()
        for repetition in range(1, args.repetitions + 1):
            invocation_id = f"invocation-{repetition}-{uuid.uuid4().hex[:8]}"
            try:
                with llm_trace_context(
                    system_evaluation_id=experiment_id,
                    evaluation_invocation_id=invocation_id,
                    evaluation_mode="efficiency",
                ):
                    run = await service.run_agent_full_flow_evaluation(
                        db,
                        case_limit=args.case_limit,
                    )
                full_runs.append(run)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                errors.append(f"repetition_{repetition}: {exc.__class__.__name__}: {exc}")
    if args.mode == "full" and args.interview_case_limit:
        if not (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")):
            raise RuntimeError("full 模式需要通过进程环境提供 LLM_API_KEY。")
        try:
            def run_interview() -> EvaluationRun:
                isolated_db = SessionLocal()
                try:
                    return EvaluationService().run_interview_prep_evaluation(
                        isolated_db,
                        case_limit=args.interview_case_limit,
                    )
                finally:
                    isolated_db.close()

            with llm_trace_context(
                system_evaluation_id=experiment_id,
                evaluation_invocation_id=f"interview-{uuid.uuid4().hex[:8]}",
                evaluation_mode="efficiency",
            ):
                interview_runs.append(await asyncio.to_thread(run_interview))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"interview: {exc.__class__.__name__}: {exc}")
    if args.reuse_interview_run_ids:
        reused_interview_ids = [
            int(item.strip()) for item in args.reuse_interview_run_ids.split(",") if item.strip()
        ]
        interview_runs = [db.get(EvaluationRun, run_id) for run_id in reused_interview_ids]
        missing = [
            str(run_id)
            for run_id, run in zip(reused_interview_ids, interview_runs, strict=False)
            if run is None
        ]
        if missing:
            raise RuntimeError(f"找不到要复用的面试 EvaluationRun: {', '.join(missing)}")
        interview_runs = [run for run in interview_runs if run is not None]

    real_report = evaluator.report_real_runs(
        db,
        full_runs,
        interview_runs=interview_runs,
        experiment_id=experiment_id,
        start_log_id=start_log_id,
    )
    cache_metrics_after_real = cache_metrics_snapshot()
    real_report["cache_metrics_delta"] = {
        key: int(cache_metrics_after_real.get(key, 0)) - int(cache_metrics_before_real.get(key, 0))
        for key in sorted(set(cache_metrics_before_real) | set(cache_metrics_after_real))
    }
    usage = real_report.get("llm_usage") or {}
    total_tokens = int(usage.get("total_tokens") or 0)
    if total_tokens > args.token_budget:
        errors.append(f"token_budget_exceeded: used={total_tokens}, budget={args.token_budget}")

    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("w", encoding="utf-8") as handle:
        for repetition, run in enumerate(full_runs, start=1):
            for case in run.case_results_json or []:
                handle.write(
                    json.dumps(
                        {
                            "experiment_id": experiment_id,
                            "repetition": repetition,
                            "evaluation_run_id": run.id,
                            "case": case,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        for run in interview_runs:
            for case in run.case_results_json or []:
                handle.write(
                    json.dumps(
                        {
                            "experiment_id": experiment_id,
                            "suite": "interview_prep",
                            "evaluation_run_id": run.id,
                            "case": case,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        for case in fault_report.get("cases") or []:
            handle.write(json.dumps({"suite": "fault_injection", "case": case}, ensure_ascii=False) + "\n")

    payload = {
        "report_version": evaluator.REPORT_VERSION,
        "experiment_id": experiment_id,
        "mode": args.mode,
        "model": os.getenv("LLM_MODEL"),
        "base_url": os.getenv("LLM_BASE_URL"),
        "repetitions": args.repetitions if args.mode == "full" else 0,
        "token_budget": args.token_budget,
        "evaluation_run_ids": [run.id for run in full_runs],
        "interview_evaluation_run_ids": [run.id for run in interview_runs],
        "fault_injection": fault_report,
        "cache_ab": cache_report,
        "real_runs": real_report,
        "errors": errors,
        "trace_path": str(trace_path),
        "wall_time_ms": round((time.perf_counter() - started) * 1000, 2),
        "release_gate_passed": (
            not errors
            and fault_report.get("pass_rate") == 1.0
            and bool(fault_report.get("release_gates", {}).get("high_risk_replan") == 0)
            and bool(fault_report.get("release_gates", {}).get("illegal_tool_blocking_rate") == 1.0)
            and bool(cache_report.get("release_gate_passed"))
            and all(
                int(value or 0) == 0
                for value in (real_report.get("release_gate") or {}).values()
                if isinstance(value, (int, float)) and "rate" not in str(value)
            )
        ),
        "interpretation": [
            "deterministic 模式只验证运行时控制器、策略边界和生产缓存，不代表 LLM 业务质量。",
            "full 模式的 Pass@1 是单次 case 通过率，Pass^K 是同一 case K 次全部通过率。",
            "延迟样本少于 10 会标记 low_sample；真实链路不足时不能据此宣称 SLO 稳定。",
            "not_exercised 表示本轮没有运行该任务类型，不能解释为成功或失败。",
        ],
    }
    write_json(output_path, payload)
    db.close()
    print(json.dumps({
        "output": str(output_path),
        "trace": str(trace_path),
        "mode": args.mode,
        "evaluation_run_ids": payload["evaluation_run_ids"],
        "case_pass_rate": real_report.get("case_pass_rate"),
        "total_tokens": total_tokens,
        "release_gate_passed": payload["release_gate_passed"],
        "errors": errors,
    }, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
