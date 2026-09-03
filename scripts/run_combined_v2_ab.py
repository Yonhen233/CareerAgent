from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_context_runtime_ab import evaluate as evaluate_context  # noqa: E402
from scripts.run_token_optimization_ab import evaluate as evaluate_token  # noqa: E402


DEFAULT_OUTPUT = ROOT / "data" / "runtime" / "combined-v2-evaluation.json"


def _real_context_latency(report: dict[str, Any], variant: str) -> float:
    rows = report.get("real_llm_results") or []
    if not rows:
        return 0.0
    return statistics.mean(float(row[variant]["latency_ms"]) for row in rows)


def _combined_metrics(
    context_report: dict[str, Any], token_report: dict[str, Any], *, real_llm: bool
) -> dict[str, Any]:
    context = context_report["metrics"]
    token = token_report["metrics"]
    if real_llm:
        baseline_input = context["v1_actual_avg_input_tokens"] + token["v1_avg_input_tokens"]
        optimized_input = context["v2_actual_avg_input_tokens"] + token["v2_avg_input_tokens"]
        baseline_total = context["v1_actual_avg_total_tokens"] + token["v1_avg_total_tokens"]
        optimized_total = context["v2_actual_avg_total_tokens"] + token["v2_avg_total_tokens"]
        baseline_cost = context["v1_avg_cost_cny"] + token["v1_avg_cost_cny"]
        optimized_cost = context["v2_avg_cost_cny"] + token["v2_avg_cost_cny"]
        baseline_latency = _real_context_latency(context_report, "v1") + token["v1_p50_latency_ms"]
        optimized_latency = _real_context_latency(context_report, "v2") + token["v2_p50_latency_ms"]
    else:
        baseline_input = context["v1_avg_input_tokens"] + token["v1_avg_input_tokens"]
        optimized_input = context["v2_avg_input_tokens"] + token["v2_avg_input_tokens"]
        baseline_total = baseline_input + token["v1_avg_output_tokens"]
        optimized_total = optimized_input + token["v2_avg_output_tokens"]
        baseline_cost = optimized_cost = None
        baseline_latency = optimized_latency = None
    baseline_calls = 1 + token["v1_calls_per_run"]
    optimized_calls = 1 + token["v2_calls_per_run"]
    return {
        "workload_definition": (
            "one structured long-context run plus one interview batch run; "
            "lane metrics remain authoritative for other traffic mixes"
        ),
        "context_case_count": context["case_count"],
        "token_case_count": token["case_count"],
        "baseline_avg_input_tokens": round(baseline_input, 3),
        "combined_v2_avg_input_tokens": round(optimized_input, 3),
        "input_token_reduction": round(1 - optimized_input / max(baseline_input, 1), 6),
        "baseline_avg_total_tokens": round(baseline_total, 3),
        "combined_v2_avg_total_tokens": round(optimized_total, 3),
        "total_token_reduction": round(1 - optimized_total / max(baseline_total, 1), 6),
        "baseline_business_calls": round(baseline_calls, 3),
        "combined_v2_business_calls": round(optimized_calls, 3),
        "business_call_reduction": round(1 - optimized_calls / max(baseline_calls, 1), 6),
        "baseline_cost_cny": round(baseline_cost, 6) if baseline_cost is not None else None,
        "combined_v2_cost_cny": round(optimized_cost, 6) if optimized_cost is not None else None,
        "cost_reduction": (
            round(1 - optimized_cost / max(baseline_cost, 0.000001), 6)
            if baseline_cost is not None
            else None
        ),
        "baseline_composed_latency_ms": (
            round(baseline_latency, 3) if baseline_latency is not None else None
        ),
        "combined_v2_composed_latency_ms": (
            round(optimized_latency, 3) if optimized_latency is not None else None
        ),
        "context_critical_fact_recall": context["critical_fact_recall"],
        "context_citation_integrity": context["citation_integrity"],
        "token_v2_critical_fact_recall": token.get("v2_critical_fact_recall"),
        "token_v2_evidence_recall": token.get("v2_evidence_recall"),
        "token_v2_forbidden_claim_free_rate": token.get("v2_forbidden_claim_free_rate"),
        "prompt_injection_escape_count": (
            context["prompt_injection_escape_count"]
            + token.get("prompt_injection_escape_count", 0)
        ),
        "cross_tenant_leakage_count": (
            context["cross_tenant_leakage_count"]
            + token.get("cross_tenant_leakage_count", 0)
        ),
        "provider_usage_complete": (
            not real_llm
            or (
                token.get("v1_usage_missing_calls", 0) == 0
                and token.get("v2_usage_missing_calls", 0) == 0
                and all(
                    row[variant]["usage_missing_calls"] == 0
                    for row in context_report.get("real_llm_results") or []
                    for variant in ("v1", "v2")
                )
            )
        ),
    }


async def evaluate(
    *, real_llm: bool, context_limit: int | None, token_limit: int | None, question_limit: int
) -> dict[str, Any]:
    context_report = await evaluate_context(
        real_llm=real_llm,
        limit=context_limit,
        combined_v2=True,
    )
    token_report = await evaluate_token(
        real_llm=real_llm,
        limit=token_limit,
        question_limit=question_limit,
        combined_v2=True,
    )
    metrics = _combined_metrics(context_report, token_report, real_llm=real_llm)
    quality_gate = (
        context_report["release_gate"]["passed"]
        and token_report["release_gate"]["quality_gate_passed"]
        and metrics["prompt_injection_escape_count"] == 0
        and metrics["cross_tenant_leakage_count"] == 0
        and metrics["provider_usage_complete"]
    )
    efficiency_gate = (
        metrics["input_token_reduction"] >= 0.40
        and metrics["business_call_reduction"] >= 0.40
    )
    return {
        "evaluation": "careeragent-context-and-token-v2-combined-ab",
        "mode": "real_llm" if real_llm else "deterministic",
        "variants": {
            "baseline": {"context_runtime_v2": False, "token_optimization_v2": False},
            "combined_v2": {"context_runtime_v2": True, "token_optimization_v2": True},
        },
        "ab_controls": {
            "same_model": True,
            "temperature": 0,
            "fallback": False,
            "same_datasets": True,
            "provider_usage_required_for_real_mode": True,
        },
        "metrics": metrics,
        "release_gate": {
            "passed": quality_gate and efficiency_gate,
            "quality_gate_passed": quality_gate,
            "efficiency_gate_passed": efficiency_gate,
            "production_default_eligible": real_llm and quality_gate and efficiency_gate,
        },
        "lane_reports": {
            "context_runtime": context_report,
            "token_optimization": token_report,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Context V2 + Token V2 combined A/B.")
    parser.add_argument("--real-llm", action="store_true")
    parser.add_argument("--context-limit", type=int)
    parser.add_argument("--token-limit", type=int)
    parser.add_argument("--question-limit", type=int, default=3)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(
        evaluate(
            real_llm=args.real_llm,
            context_limit=args.context_limit,
            token_limit=args.token_limit,
            question_limit=args.question_limit,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"output": str(args.output), **report["metrics"], "release_gate": report["release_gate"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
