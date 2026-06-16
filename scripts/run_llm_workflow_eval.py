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
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from app.core.database import SessionLocal
from app.core.database import init_db
from app.services.evaluation_service import EvaluationService


def _parse_case_indexes(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    indexes: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            indexes.append(int(item))
    return indexes or None


def _compact_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": case.get("name"),
        "difficulty": case.get("difficulty"),
        "status": case.get("status"),
        "case_passed": case.get("case_passed"),
        "failed_stage": case.get("failed_stage"),
        "expected_fit_label": case.get("expected_fit_label"),
        "predicted_fit_label": case.get("predicted_fit_label"),
        "predicted_fit_score": case.get("predicted_fit_score"),
        "tailor_passed": case.get("tailor_passed"),
        "guardrail_passed": case.get("guardrail_passed"),
    }


async def _run(args: argparse.Namespace) -> int:
    init_db()
    db = SessionLocal()
    try:
        run = await EvaluationService().run_llm_workflow_evaluation(
            db,
            case_limit=args.case_limit,
            case_indexes=_parse_case_indexes(args.case_indexes),
            trace_path=Path(args.trace_path) if args.trace_path else None,
            resume_from_last_completed=args.resume,
        )
        payload = {
            "run_id": run.id,
            "summary": run.summary_json,
            "cases": [_compact_case(case) for case in run.case_results_json],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.allow_failures:
            return 0
        return 0 if run.summary_json.get("status") == "completed" else 1
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real LLM workflow evaluation with checkpoint trace.")
    parser.add_argument("--case-limit", type=int, default=None, help="Only run the first N selected cases.")
    parser.add_argument("--case-indexes", default=None, help="Comma-separated 0-based case indexes to run.")
    parser.add_argument(
        "--trace-path",
        default="data/runtime/llm_workflow_trace_latest.jsonl",
        help="JSONL checkpoint trace path. Existing file is overwritten unless --resume is set.",
    )
    parser.add_argument("--resume", action="store_true", help="Resume from the first missing case in trace-path.")
    parser.add_argument("--allow-failures", action="store_true", help="Exit 0 even if quality gates fail.")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
