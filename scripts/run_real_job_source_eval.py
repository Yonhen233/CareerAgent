import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.core.database import SessionLocal, init_db  # noqa: E402
from app.services.evaluation_service import EvaluationService  # noqa: E402


METRIC_EXPECTATIONS = {
    "min_reachable_source_rate": "reachable_source_rate",
    "min_result_source_rate": "result_source_rate",
    "min_non_empty_jd_rate": "non_empty_jd_rate",
    "min_apply_url_rate": "apply_url_rate",
    "min_internship_like_rate": "internship_like_rate",
    "min_query_relevance_rate": "query_relevance_rate",
    "min_agent_related_rate": "agent_related_rate",
}


def _gate_failures(summary: dict[str, Any], expectations: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for expectation_name, metric_name in METRIC_EXPECTATIONS.items():
        if expectation_name not in expectations:
            continue
        actual = float(summary.get(metric_name) or 0.0)
        expected = float(expectations[expectation_name])
        if actual < expected:
            failures.append(f"{metric_name}={actual:.4f} < {expected:.4f}")
    return failures


async def _run(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset)
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    if args.case_limit:
        cases = cases[: args.case_limit]
    source_override = [item.strip() for item in args.sources.split(",") if item.strip()] if args.sources else None

    init_db()
    db = SessionLocal()
    results: list[dict[str, Any]] = []
    try:
        for case in cases:
            run = await EvaluationService().run_real_job_source_smoke(
                db,
                query=case["query"],
                location=case.get("location"),
                limit=int(case.get("limit") or 8),
                sources=source_override or case.get("sources"),
            )
            failures = _gate_failures(run.summary_json, case.get("expectations") or {})
            results.append(
                {
                    "name": case["name"],
                    "run_id": run.id,
                    "status": run.summary_json["status"],
                    "passed": not failures and run.summary_json["total_result_count"] > 0,
                    "gate_failures": failures,
                    "summary": run.summary_json,
                }
            )
    finally:
        db.close()

    passed_count = sum(1 for result in results if result["passed"])
    payload = {
        "evaluation_type": "real_job_source_suite",
        "dataset": str(dataset_path),
        "status": "completed" if passed_count == len(results) else "completed_with_quality_failures",
        "case_count": len(results),
        "passed_count": passed_count,
        "pass_rate": round(passed_count / max(len(results), 1), 4),
        "results": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.allow_source_failures:
        return 0
    return 0 if payload["status"] == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run multi-query smoke evaluation against real Chinese job sources.")
    parser.add_argument("--dataset", default="evals/real_job_source_cases.json")
    parser.add_argument("--case-limit", type=int, default=None)
    parser.add_argument("--sources", default=None, help="Optional comma-separated source override.")
    parser.add_argument(
        "--allow-source-failures",
        action="store_true",
        help="Keep exit code 0 when external source or quality gates fail.",
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
