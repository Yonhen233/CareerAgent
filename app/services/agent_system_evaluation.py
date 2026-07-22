from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import EvaluationRun, LLMCallLog


DEEPSEEK_PRICING_CNY_PER_MILLION_TOKENS: dict[str, dict[str, float]] = {
    "deepseek-v4-flash": {"cached_input": 0.02, "uncached_input": 1.0, "output": 2.0},
    "deepseek-v4-pro": {"cached_input": 0.025, "uncached_input": 3.0, "output": 6.0},
}

SUITE_METRIC_KEYS = (
    "status",
    "case_count",
    "pass_rate",
    "accuracy",
    "completed_rate",
    "end_to_end_pass_rate",
    "top_job_accuracy",
    "trace_pass_rate",
    "artifact_pass_rate",
    "langgraph_pass_rate",
    "avg_action_precision",
    "avg_action_recall",
    "avg_required_skill_precision",
    "avg_required_skill_recall",
    "top1_accuracy",
    "avg_top3_recall",
    "avg_top5_recall",
    "avg_mrr",
    "avg_ndcg_at_5",
    "fit_label_accuracy",
    "fit_score_in_range_rate",
    "tailor_pass_rate",
    "forbidden_claim_free_rate",
    "detection_recall",
    "false_positive_rate",
    "positive_recall",
    "question_answering_accuracy",
    "nonresponsive_answer_false_accept_rate",
    "specificity",
    "strategy_recall",
    "required_skill_coverage_rate",
    "quick_apply_pass_rate",
    "application_packet_pass_rate",
    "question_quality_pass_rate",
    "source_backed_pass_rate",
    "avg_question_quality_score",
)


class AgentSystemEvaluationReporter:
    def __init__(self, *, base_path: Path, experiment_id: str) -> None:
        self.base_path = base_path
        self.experiment_id = experiment_id

    def dataset_manifest(self) -> dict[str, Any]:
        manifest: dict[str, Any] = {}
        for path in sorted((self.base_path / "evals").glob("*.json")):
            if path.name.endswith("_policy.json"):
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload if isinstance(payload, list) else payload.get("cases") or payload.get("scenarios") or []
            if not isinstance(rows, list):
                rows = []
            manifest[path.name] = {
                "case_count": len(rows),
                "difficulty": self._distribution(rows, "difficulty"),
                "noise_profile": self._distribution(rows, "noise_profile"),
                "source_type": self._distribution(rows, "source_type"),
            }
        return manifest

    def compact_suite(self, run: EvaluationRun) -> dict[str, Any]:
        summary = run.summary_json or {}
        metrics = {key: summary[key] for key in SUITE_METRIC_KEYS if key in summary}
        selected_metrics = summary.get("selected_metrics")
        if isinstance(selected_metrics, dict):
            metrics["selected_strategy"] = summary.get("selected_strategy") or summary.get("selected")
            for key in (
                "top1_accuracy",
                "avg_top3_recall",
                "avg_top5_recall",
                "avg_mrr",
                "avg_ndcg_at_5",
                "top3_keyword_hit_rate",
                "top3_page_hit_rate",
                "top3_context_hit_rate",
                "actual_embedding_providers",
                "actual_reranker_providers",
                "fallback_reasons",
            ):
                if key in selected_metrics:
                    metrics[key] = selected_metrics[key]
        return {
            "evaluation_run_id": run.id,
            "name": run.name,
            "gate_passed": self.release_gate_passed(summary),
            "metrics": metrics,
        }

    def usage_report(self, db: Session, *, start_log_id: int) -> dict[str, Any]:
        rows = list(
            db.scalars(select(LLMCallLog).where(LLMCallLog.id > start_log_id).order_by(LLMCallLog.id))
        )
        rows = [
            row
            for row in rows
            if (row.context_json or {}).get("system_evaluation_id") == self.experiment_id
        ]
        model_rows: dict[str, list[LLMCallLog]] = {}
        route_rows: dict[str, list[LLMCallLog]] = {}
        trace_rows: dict[str, list[LLMCallLog]] = {}
        for row in rows:
            model_rows.setdefault(row.model, []).append(row)
            route = str((row.context_json or {}).get("model_route") or "unclassified")
            route_rows.setdefault(route, []).append(row)
            trace_rows.setdefault(self._trace_group(row.trace_name), []).append(row)

        return {
            "call_count": len(rows),
            "completed_call_count": sum(row.status == "completed" for row in rows),
            "failed_call_count": sum(row.status != "completed" for row in rows),
            "call_success_rate": self._ratio(sum(row.status == "completed" for row in rows), len(rows)),
            "prompt_tokens": sum(int(row.prompt_tokens or 0) for row in rows),
            "completion_tokens": sum(int(row.completion_tokens or 0) for row in rows),
            "total_tokens": sum(int(row.total_tokens or 0) for row in rows),
            "cache_hit_prompt_tokens": sum(self._provider_usage(row, "prompt_cache_hit_tokens") for row in rows),
            "cache_miss_prompt_tokens": sum(
                self._provider_usage(row, "prompt_cache_miss_tokens") for row in rows
            ),
            "usage_detail_coverage_rate": self._ratio(
                sum(bool((row.context_json or {}).get("provider_usage")) for row in rows),
                len(rows),
            ),
            "latency_ms": self._latency_summary(int(row.latency_ms or 0) for row in rows),
            "cost_cny": self._cost_range(rows),
            "by_model": {
                model: self._usage_group(group) for model, group in sorted(model_rows.items())
            },
            "by_route": {
                route: self._usage_group(group) for route, group in sorted(route_rows.items())
            },
            "by_trace_group": {
                trace: self._usage_group(group) for trace, group in sorted(trace_rows.items())
            },
            "errors": [
                {
                    "log_id": row.id,
                    "trace_name": row.trace_name,
                    "model": row.model,
                    "status": row.status,
                    "error": row.error_message,
                }
                for row in rows
                if row.status != "completed"
            ],
        }

    def trajectory_report(self, runs: Iterable[EvaluationRun]) -> dict[str, Any]:
        cases = [case for run in runs for case in (run.case_results_json or [])]
        traces = [trace for case in cases for trace in (case.get("run_trace") or [])]
        steps = [step for trace in traces for step in (trace.get("steps") or [])]
        tool_steps = [step for step in steps if step.get("tool_name")]
        tool_names = Counter(str(step.get("tool_name")) for step in tool_steps)
        return {
            "trace_count": len(traces),
            "step_count": len(steps),
            "step_success_rate": self._ratio(sum(step.get("status") == "completed" for step in steps), len(steps)),
            "tool_call_count": len(tool_steps),
            "tool_success_rate": self._ratio(
                sum(step.get("status") == "completed" for step in tool_steps),
                len(tool_steps),
            ),
            "failed_tool_call_count": sum(step.get("status") != "completed" for step in tool_steps),
            "tool_latency_ms": self._latency_summary(int(step.get("latency_ms") or 0) for step in tool_steps),
            "tool_call_breakdown": dict(sorted(tool_names.items())),
        }

    def reliability_report(
        self,
        repeated_case_results: list[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        if not repeated_case_results:
            return {"repetitions": 0, "case_count": 0, "pass_at_1": None, "pass_power_k": None}
        by_case: dict[str, list[bool]] = {}
        for repetition in repeated_case_results:
            for case in repetition:
                name = str(case.get("name") or case.get("case_id") or "unknown")
                by_case.setdefault(name, []).append(bool(case.get("case_passed") or case.get("passed")))
        repetitions = max((len(values) for values in by_case.values()), default=0)
        all_trials = [value for values in by_case.values() for value in values]
        complete_cases = [values for values in by_case.values() if len(values) == repetitions]
        return {
            "repetitions": repetitions,
            "case_count": len(by_case),
            "trial_count": len(all_trials),
            "pass_at_1": self._ratio(sum(all_trials), len(all_trials)),
            "pass_power_k": self._ratio(sum(all(values) for values in complete_cases), len(complete_cases)),
            "per_case": {name: values for name, values in sorted(by_case.items())},
            "definition": "pass^k 为同一 case 的 k 次运行全部通过的比例；它衡量一致性，不等同于至少一次成功。",
        }

    def build_summary(
        self,
        *,
        mode: str,
        suites: dict[str, EvaluationRun],
        suite_errors: dict[str, str],
        usage: dict[str, Any],
        wall_time_ms: int,
        reliability: dict[str, Any] | None = None,
        required_suites: Iterable[str] = (),
    ) -> dict[str, Any]:
        compact = {name: self.compact_suite(run) for name, run in suites.items()}
        required = list(required_suites)
        reliability_report = reliability or self.reliability_report([])
        release_checks = [
            {
                "suite": name,
                "passed": bool(compact.get(name, {}).get("gate_passed")),
                "error": suite_errors.get(name),
            }
            for name in required
        ]
        if mode == "full" and reliability_report.get("repetitions", 0) >= 2:
            reliability_passed = float(reliability_report.get("pass_power_k") or 0.0) >= 0.8
            release_checks.append(
                {
                    "suite": "workflow_reliability_pass_power_k",
                    "passed": reliability_passed,
                    "actual": reliability_report.get("pass_power_k"),
                    "threshold": 0.8,
                    "error": None,
                }
            )
        full_flow_runs = [run for name, run in suites.items() if name == "agent_full_flow"]
        return {
            "evaluation_type": "agent_system",
            "methodology_version": "careeragent-agent-eval-v1",
            "experiment_id": self.experiment_id,
            "mode": mode,
            "status": "completed" if not suite_errors else "completed_with_suite_errors",
            "suite_count": len(suites),
            "dataset_manifest": self.dataset_manifest(),
            "suites": compact,
            "suite_errors": suite_errors,
            "trajectory": self.trajectory_report(full_flow_runs),
            "reliability": reliability_report,
            "performance": {
                "wall_time_ms": wall_time_ms,
                "llm": usage,
                "amortized_system_cost_per_successful_workflow_case_cny": self._cost_per_success(
                    suites,
                    usage,
                ),
            },
            "release_gate": {
                "passed": bool(release_checks) and all(item["passed"] for item in release_checks),
                "checks": release_checks,
                "required_suites": required,
            },
            "interpretation": [
                "系统不计算掩盖短板的加权总分；任一核心 suite 未过门禁，系统发布门禁即失败。",
                "外部岗位源属于可用性 smoke，网络波动单独报告，不参与核心模型质量门禁。",
                "LLM 成本优先使用供应商缓存明细；旧接口未返回缓存拆分时同时给出成本下界和上界。",
                "合成和可控数据适合回归，不替代真实用户流量、人审校准和线上持续评测。",
            ],
        }

    @staticmethod
    def release_gate_passed(summary: dict[str, Any]) -> bool:
        gate = summary.get("release_gate")
        if isinstance(gate, dict) and "passed" in gate:
            return bool(gate["passed"])
        if "passed" in summary:
            return bool(summary["passed"])
        return False

    @staticmethod
    def _distribution(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
        counts = Counter(str(row.get(key) or "unspecified") for row in rows if isinstance(row, dict))
        return dict(sorted(counts.items())) if counts and counts != {"unspecified": len(rows)} else {}

    @staticmethod
    def _ratio(numerator: int | float, denominator: int | float) -> float:
        return round(float(numerator) / float(denominator), 4) if denominator else 0.0

    @classmethod
    def _percentile(cls, values: Iterable[int | float], percentile: float) -> float:
        ordered = sorted(float(value) for value in values)
        if not ordered:
            return 0.0
        rank = (len(ordered) - 1) * percentile
        lower = math.floor(rank)
        upper = math.ceil(rank)
        if lower == upper:
            return round(ordered[lower], 2)
        return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower), 2)

    @classmethod
    def _latency_summary(cls, values: Iterable[int | float]) -> dict[str, float | int]:
        rows = list(values)
        return {
            "count": len(rows),
            "mean": round(sum(rows) / len(rows), 2) if rows else 0.0,
            "p50": cls._percentile(rows, 0.5),
            "p95": cls._percentile(rows, 0.95),
            "max": max(rows, default=0),
        }

    @staticmethod
    def _provider_usage(row: LLMCallLog, key: str) -> int:
        try:
            return max(0, int(((row.context_json or {}).get("provider_usage") or {}).get(key) or 0))
        except (TypeError, ValueError):
            return 0

    def _usage_group(self, rows: list[LLMCallLog]) -> dict[str, Any]:
        return {
            "call_count": len(rows),
            "completed_call_count": sum(row.status == "completed" for row in rows),
            "prompt_tokens": sum(int(row.prompt_tokens or 0) for row in rows),
            "completion_tokens": sum(int(row.completion_tokens or 0) for row in rows),
            "total_tokens": sum(int(row.total_tokens or 0) for row in rows),
            "latency_ms": self._latency_summary(int(row.latency_ms or 0) for row in rows),
            "cost_cny": self._cost_range(rows),
        }

    @staticmethod
    def _trace_group(trace_name: str) -> str:
        parts = str(trace_name or "unknown").split(".")
        if parts[:2] == ["evaluation", "interview_claim_verifier"]:
            return "evaluation.interview_claim_verifier"
        if len(parts) >= 2:
            return ".".join(parts[:2])
        return parts[0] if parts else "unknown"

    def _cost_range(self, rows: list[LLMCallLog]) -> dict[str, Any]:
        lower = 0.0
        upper = 0.0
        priced_calls = 0
        cache_detailed_calls = 0
        for row in rows:
            pricing = DEEPSEEK_PRICING_CNY_PER_MILLION_TOKENS.get(row.model.lower())
            if not pricing:
                continue
            priced_calls += 1
            prompt_tokens = max(0, int(row.prompt_tokens or 0))
            completion_tokens = max(0, int(row.completion_tokens or 0))
            hit = self._provider_usage(row, "prompt_cache_hit_tokens")
            miss = self._provider_usage(row, "prompt_cache_miss_tokens")
            known = min(prompt_tokens, hit + miss)
            unknown = max(0, prompt_tokens - known)
            if prompt_tokens == 0 or hit or miss:
                cache_detailed_calls += 1
            output_cost = completion_tokens * pricing["output"] / 1_000_000
            lower += (
                hit * pricing["cached_input"]
                + miss * pricing["uncached_input"]
                + unknown * pricing["cached_input"]
            ) / 1_000_000 + output_cost
            upper += (
                hit * pricing["cached_input"]
                + miss * pricing["uncached_input"]
                + unknown * pricing["uncached_input"]
            ) / 1_000_000 + output_cost
        exact = priced_calls > 0 and cache_detailed_calls == priced_calls and abs(lower - upper) < 1e-9
        return {
            "lower_bound": round(lower, 6),
            "upper_bound": round(upper, 6),
            "exact": exact,
            "priced_call_count": priced_calls,
            "cache_detailed_call_count": cache_detailed_calls,
            "currency": "CNY",
            "pricing_basis": "2026-07-22 DeepSeek V4 Flash/Pro public rates supplied for this project",
        }

    def _cost_per_success(self, suites: dict[str, EvaluationRun], usage: dict[str, Any]) -> dict[str, Any]:
        workflow = suites.get("llm_workflow")
        if workflow is None:
            return {"successful_workflow_cases": 0, "lower_bound": None, "upper_bound": None}
        successes = sum(bool(case.get("case_passed")) for case in (workflow.case_results_json or []))
        cost = usage.get("cost_cny") or {}
        return {
            "successful_workflow_cases": successes,
            "lower_bound": round(float(cost.get("lower_bound") or 0) / successes, 6) if successes else None,
            "upper_bound": round(float(cost.get("upper_bound") or 0) / successes, 6) if successes else None,
            "scope": "整轮系统实验总成本除以 llm_workflow 成功 case 数，仅用于实验预算摊销，不代表单个工作流独立成本。",
        }
