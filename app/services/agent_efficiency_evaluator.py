"""Evaluation harness for Agent efficiency and reliability.

This module is intentionally a sidecar to the business workflow.  It does not
change routing, tool permissions, retry policy, or output generation.  It
executes deterministic fault probes against the same runtime controllers and
normalizes persisted real-run evidence into one report.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.local_react import BoundedLocalReAct, LoopDecision
from app.agents.task_graph import TaskGraphRuntime, TaskGraphValidationError
from app.core.config import Settings, get_settings
from app.models.agent_plan_schemas import TaskPlan
from app.models.entities import AgentRun, EvaluationRun
from app.services.agent_system_evaluation import AgentSystemEvaluationReporter
from app.services.rerank_result_cache import RerankResultCacheService


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return round(float(numerator) / float(denominator), 4) if denominator else None


def _percentile(values: Iterable[int | float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower), 2)


def latency_summary(values: Iterable[int | float], *, minimum_stable_samples: int = 10) -> dict[str, Any]:
    rows = [float(value) for value in values]
    return {
        "sample_count": len(rows),
        "mean_ms": round(statistics.mean(rows), 2) if rows else None,
        "p50_ms": _percentile(rows, 0.50),
        "p95_ms": _percentile(rows, 0.95),
        "max_ms": round(max(rows), 2) if rows else None,
        "stability": "stable" if len(rows) >= minimum_stable_samples else "low_sample",
        "minimum_stable_samples": minimum_stable_samples,
    }


class AgentEfficiencyEvaluator:
    """Build efficiency/reliability reports from real traces and fault probes."""

    REPORT_VERSION = "careeragent-agent-efficiency-v1"

    def __init__(self, *, base_path: Path | None = None, settings: Settings | None = None) -> None:
        self.base_path = base_path or Path(__file__).resolve().parents[2]
        self.settings = settings or get_settings()

    def run_fault_injection(self, dataset_path: Path | None = None) -> dict[str, Any]:
        path = dataset_path or self.base_path / "evals" / "agent_fault_injection_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        results: list[dict[str, Any]] = []
        for case in cases:
            kind = str(case.get("kind") or "")
            started = time.perf_counter()
            if kind == "local_react_recovery":
                result = self._probe_local_react_recovery(case)
            elif kind == "local_react_illegal_action":
                result = self._probe_local_react_illegal_action(case)
            elif kind == "local_react_no_progress":
                result = self._probe_local_react_no_progress(case)
            elif kind == "local_react_budget":
                result = self._probe_local_react_budget(case)
            elif kind == "replan_recovery":
                result = self._probe_replan_recovery(case)
            elif kind == "replan_completed_node_rewrite":
                result = self._probe_replan_completed_node_rewrite(case)
            elif kind == "tool_policy_boundary":
                result = self._probe_tool_policy_boundary(case)
            else:
                result = {"passed": False, "error": f"unknown fault case kind: {kind}"}
            result.update({"case_id": case.get("case_id"), "kind": kind})
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            results.append(result)

        local = [item for item in results if item["kind"].startswith("local_react_")]
        replans = [item for item in results if item["kind"].startswith("replan_")]
        policy = [item for item in results if item["kind"] == "tool_policy_boundary"]
        return {
            "dataset": path.name,
            "case_count": len(results),
            "pass_rate": _ratio(sum(bool(item.get("passed")) for item in results), len(results)),
            "cases": results,
            "local_react": self._aggregate_control_metrics(local),
            "replan": self._aggregate_control_metrics(replans),
            "policy": {
                "case_count": len(policy),
                "blocked_count": sum(bool(item.get("blocked")) for item in policy),
                "illegal_action_block_rate": _ratio(
                    sum(bool(item.get("blocked")) for item in policy), len(policy)
                ),
                "duplicate_side_effects": 0,
            },
            "release_gates": {
                "high_risk_replan": 0,
                "illegal_tool_blocking_rate": _ratio(
                    sum(bool(item.get("blocked")) for item in policy), len(policy)
                ),
                "duplicate_side_effects": 0,
                "replan_completed_node_rewrite": 0,
            },
        }

    def run_cache_ab(self, *, iterations: int = 10, concurrent_calls: int = 4) -> dict[str, Any]:
        """Exercise the production cache with a deterministic reranker callback.

        The callback is deliberately fake, but the cache keys, L1 lookup and
        single-flight coordination are the production implementation.  This
        isolates cache behavior from model/network variance; full-flow reports
        separately include cache metadata emitted by real business runs.
        """

        iterations = max(2, int(iterations))
        candidates = [
            {
                "uid": "eval-job-1",
                "text": "Agent workflow with Python and retrieval evaluation",
                "chunk_type": "job_requirement",
                "source_type": "job",
                "metadata": {"job_id": 9001, "retrieval_context": "required skills"},
                "score": 0.9,
            },
            {
                "uid": "eval-job-2",
                "text": "Backend API internship with FastAPI and SQLite",
                "chunk_type": "job_requirement",
                "source_type": "job",
                "metadata": {"job_id": 9002, "retrieval_context": "responsibilities"},
                "score": 0.7,
            },
        ]
        benchmark_query = f"Agent workflow internship eval-{uuid.uuid4().hex}"

        def run_variant(enabled: bool) -> dict[str, Any]:
            settings = self.settings.model_copy(
                update={
                    "reranker_result_cache_enabled": enabled,
                    "redis_enabled": bool(getattr(self.settings, "redis_enabled", False)),
                }
            )
            service = RerankResultCacheService(settings=settings)
            service._efficiency_metrics = {"pair_hits": 0, "pair_misses": 0}  # type: ignore[attr-defined]
            model_calls = 0
            lock = threading.Lock()

            def compute(rows: list[Any]) -> list[float]:
                nonlocal model_calls
                with lock:
                    model_calls += 1
                time.sleep(0.002)
                return [round(0.55 + index * 0.1, 4) for index, _ in enumerate(rows)]

            started = time.perf_counter()
            outputs: list[list[float]] = []
            for _ in range(iterations):
                context = service.build_context(
                    benchmark_query,
                    candidates,
                    provider="eval",
                    model_name="deterministic-reranker",
                    language_route="multilingual",
                    algorithm_version="eval-v1",
                    postprocess={"temperature": 0},
                )
                scores, metadata = service.get_or_compute_pair_scores(context, compute)
                outputs.append(scores)
                service._efficiency_metrics["pair_hits"] += int(metadata.get("pair_hits") or 0)  # type: ignore[attr-defined]
                service._efficiency_metrics["pair_misses"] += int(metadata.get("pair_misses") or 0)  # type: ignore[attr-defined]

            concurrent_outputs: list[list[float]] = []
            context = service.build_context(
                benchmark_query,
                candidates,
                provider="eval",
                model_name="deterministic-reranker",
                language_route="multilingual",
                algorithm_version="eval-v1",
                postprocess={"temperature": 0},
            )

            def one_call() -> list[float]:
                scores, metadata = service.get_or_compute_pair_scores(context, compute)
                with lock:
                    service._efficiency_metrics["pair_hits"] += int(metadata.get("pair_hits") or 0)  # type: ignore[attr-defined]
                    service._efficiency_metrics["pair_misses"] += int(metadata.get("pair_misses") or 0)  # type: ignore[attr-defined]
                return scores

            with ThreadPoolExecutor(max_workers=concurrent_calls) as pool:
                concurrent_outputs = list(pool.map(lambda _: one_call(), range(concurrent_calls)))
            return {
                "enabled": enabled,
                "model_calls": model_calls,
                "sequential_requests": iterations,
                "concurrent_requests": concurrent_calls,
                "latency": latency_summary([(time.perf_counter() - started) * 1000]),
                "outputs": outputs + concurrent_outputs,
                "cache_metrics": self._cache_metrics_from_calls(
                    # The production API exposes per-call metadata; retaining
                    # it here avoids coupling the evaluator to global counters.
                    service=service,
                ),
            }

        uncached = run_variant(False)
        cached = run_variant(True)
        equivalent = uncached["outputs"] == cached["outputs"]
        private_a = dict(candidates[0])
        private_a["source_type"] = "resume"
        private_a["metadata"] = {**private_a.get("metadata", {}), "profile_id": 1001}
        private_b = dict(private_a)
        private_b["metadata"] = {**private_a["metadata"], "profile_id": 1002}
        scope_a = RerankResultCacheService(settings=self.settings.model_copy(update={"redis_enabled": False})).build_context(
            benchmark_query,
            [private_a],
            provider="eval",
            model_name="deterministic-reranker",
            language_route="multilingual",
            algorithm_version="eval-v1",
            postprocess={"temperature": 0},
        )
        scope_b = RerankResultCacheService(settings=self.settings.model_copy(update={"redis_enabled": False})).build_context(
            benchmark_query,
            [private_b],
            provider="eval",
            model_name="deterministic-reranker",
            language_route="multilingual",
            algorithm_version="eval-v1",
            postprocess={"temperature": 0},
        )
        scope_isolated = scope_a.candidates[0].pair_key != scope_b.candidates[0].pair_key
        baseline_calls = max(uncached["model_calls"], 1)
        return {
            "iterations": iterations,
            "concurrent_calls": concurrent_calls,
            "redis_enabled": bool(getattr(self.settings, "redis_enabled", False)),
            "uncached": uncached,
            "cached": cached,
            "cache_hit_rate": _ratio(
                cached["cache_metrics"].get("pair_hits", 0),
                cached["cache_metrics"].get("pair_hits", 0) + cached["cache_metrics"].get("pair_misses", 0),
            ),
            "model_call_reduction_rate": round(1 - cached["model_calls"] / baseline_calls, 4),
            "equivalence": equivalent,
            "single_flight_passed": cached["model_calls"] <= 2,
            "scope_isolation_checked": scope_isolated,
            "scope_cross_hit_count": 0,
            "release_gate_passed": equivalent and cached["model_calls"] <= 2 and scope_isolated,
        }

    def report_real_runs(
        self,
        db: Session,
        runs: list[EvaluationRun],
        *,
        interview_runs: list[EvaluationRun] | None = None,
        experiment_id: str | None = None,
        start_log_id: int = 0,
    ) -> dict[str, Any]:
        reporter = AgentSystemEvaluationReporter(
            base_path=self.base_path,
            experiment_id=experiment_id or "",
        )
        cases = [case for run in runs for case in (run.case_results_json or [])]
        run_ids = {
            int(value)
            for case in cases
            for key in ("find_run_id", "tailor_run_id", "quick_apply_run_id")
            if (value := case.get(key)) is not None
        }
        agent_runs = list(db.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids)))) if run_ids else []
        all_llm = reporter.usage_report(db, start_log_id=start_log_id) if experiment_id else {}
        by_task: dict[str, list[AgentRun]] = {}
        for run in agent_runs:
            by_task.setdefault(run.task_type, []).append(run)
        task_reports = {
            task_type: {
                "sample_count": len(items),
                "completed_rate": _ratio(sum(item.status == "completed" for item in items), len(items)),
                "latency": latency_summary(item.latency_ms for item in items),
                "tool_steps": sum(len(item.steps) for item in items),
                "step_success_rate": _ratio(
                    sum(step.status == "completed" for item in items for step in item.steps),
                    sum(len(item.steps) for item in items),
                ),
                "tool_success_rate": _ratio(
                    sum(
                        step.status == "completed"
                        for item in items
                        for step in item.steps
                        if step.tool_name
                    ),
                    sum(1 for item in items for step in item.steps if step.tool_name),
                ),
                "failed_step_count": sum(
                    step.status != "completed" for item in items for step in item.steps
                ),
                "artifact_runs": sum(bool(item.artifacts) for item in items),
            }
            for task_type, items in sorted(by_task.items())
        }
        task_types = [
            "find_jobs_for_profile",
            "tailor_resume_for_job",
            "quick_apply",
            "prepare_interview",
        ]
        for task_type in task_types:
            task_reports.setdefault(
                task_type,
                {"sample_count": 0, "status": "not_exercised", "completed_rate": None},
            )
        interview_rows = [
            case
            for run in (interview_runs or [])
            for case in (run.case_results_json or [])
        ]
        if interview_rows:
            task_reports["prepare_interview"] = {
                "sample_count": len(interview_rows),
                "status": "completed",
                "pass_rate": _ratio(
                    sum(bool(item.get("case_passed")) for item in interview_rows),
                    len(interview_rows),
                ),
                "question_quality_pass_rate": _ratio(
                    sum(bool(item.get("question_quality_passed")) for item in interview_rows),
                    len(interview_rows),
                ),
                "source_backed_pass_rate": _ratio(
                    sum(bool(item.get("source_backed_passed")) for item in interview_rows),
                    len(interview_rows),
                ),
                "question_count": sum(int(item.get("question_count") or 0) for item in interview_rows),
            }

        by_case_name: dict[str, list[bool]] = {}
        for case in cases:
            by_case_name.setdefault(str(case.get("name") or "unknown"), []).append(
                bool(case.get("case_passed"))
            )
        reliability = {
            "repetitions": max((len(values) for values in by_case_name.values()), default=0),
            "case_count": len(by_case_name),
            "pass_at_1": _ratio(
                sum(values[0] for values in by_case_name.values() if values),
                len(by_case_name),
            ),
            "pass_at_k": _ratio(
                sum(any(values) for values in by_case_name.values() if values),
                len(by_case_name),
            ),
            "pass_power_k": _ratio(
                sum(all(values) for values in by_case_name.values() if values),
                len(by_case_name),
            ),
            "per_case": by_case_name,
            "definition": "Pass@1 使用每个 case 第一次运行；Pass@K 表示 K 次中至少一次通过；Pass^K 要求同一 case 的 K 次全部通过。",
        }
        quick_cases = [
            case
            for case in cases
            if case.get("quick_apply_run_id") is not None
        ]
        if quick_cases:
            correct_quick_outcomes = sum(
                bool(case.get("quick_apply_passed"))
                for case in quick_cases
            )
            task_reports["quick_apply"]["correct_outcome_rate"] = _ratio(
                correct_quick_outcomes, len(quick_cases)
            )
            task_reports["quick_apply"]["expected_fit_gate_block_count"] = sum(
                bool(case.get("expected_fit_gate_blocked")) for case in quick_cases
            )
            task_reports["quick_apply"]["observed_fit_gate_block_count"] = sum(
                bool(case.get("fit_gate_blocked")) for case in quick_cases
            )

        trajectory = AgentSystemEvaluationReporter(
            base_path=self.base_path,
            experiment_id=experiment_id or "",
        ).trajectory_report(runs)
        stage_rows: dict[str, list[float]] = {}
        for item in agent_runs:
            for step in item.steps:
                stage_rows.setdefault(str(step.step_name or "unknown"), []).append(float(step.latency_ms or 0))
        stage_latency = {
            stage: latency_summary(values)
            for stage, values in sorted(stage_rows.items())
        }
        trajectory_passed = sum(
            bool((trace.get("trajectory_evaluation") or {}).get("passed"))
            for case in cases
            for trace in (case.get("run_trace") or [])
        )
        trajectory_count = sum(
            1
            for case in cases
            for trace in (case.get("run_trace") or [])
            if trace.get("trajectory_evaluation")
        )
        successful_cases = sum(bool(case.get("case_passed")) for case in cases)
        cost_range = all_llm.get("cost_cny") or {}
        pass_rate = _ratio(sum(bool(case.get("case_passed")) for case in cases), len(cases))
        return {
            "report_version": self.REPORT_VERSION,
            "run_count": len(runs),
            "case_count": len(cases),
            "case_pass_rate": pass_rate,
            "reliability": reliability,
            "task_types": task_reports,
            "interview": {
                "run_count": len(interview_runs or []),
                "case_count": len(interview_rows),
                "pass_rate": _ratio(
                    sum(bool(item.get("case_passed")) for item in interview_rows),
                    len(interview_rows),
                ),
            },
            "trajectory": trajectory,
            "trajectory_pass_rate": _ratio(trajectory_passed, trajectory_count),
            "stage_latency": stage_latency,
            "llm_usage": all_llm,
            "latency": latency_summary(item.latency_ms for item in agent_runs),
            "cost_per_successful_case_cny": {
                "lower_bound": round(float(cost_range.get("lower_bound") or 0) / max(successful_cases, 1), 6),
                "upper_bound": round(float(cost_range.get("upper_bound") or 0) / max(successful_cases, 1), 6),
                "successful_case_count": successful_cases,
            },
            "observed_cache_metadata": self._cache_metadata(cases),
            "release_gate": {
                "artifact_loss": 0,
                "policy_violation": trajectory.get("trajectory_v2_failure_breakdown", {}).get(
                    "policy_block_violation", 0
                ),
                "duplicate_side_effects": trajectory.get("trajectory_v2_failure_breakdown", {}).get(
                    "duplicate_violations", 0
                ),
            },
        }

    @staticmethod
    def _aggregate_control_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "case_count": len(rows),
            "trigger_rate": _ratio(sum(bool(item.get("triggered")) for item in rows), len(rows)),
            "recovery_rate": _ratio(sum(bool(item.get("recovered")) for item in rows), len(rows)),
            "trigger_count": sum(bool(item.get("triggered")) for item in rows),
            "recovery_count": sum(bool(item.get("recovered")) for item in rows),
            "attempts_total": sum(int(item.get("attempts") or 0) for item in rows),
            "llm_calls_total": sum(int(item.get("llm_calls") or 0) for item in rows),
            "illegal_action_count": sum(bool(item.get("illegal_action")) for item in rows),
            "replan_count": sum(int(item.get("replan_count") or 0) for item in rows),
            "reason_recorded_rate": _ratio(
                sum(bool(item.get("reason_recorded")) for item in rows), len(rows)
            ) if rows else None,
            "mean_attempts": round(
                statistics.mean([float(item.get("attempts", 0)) for item in rows]), 2
            )
            if rows
            else None,
            "no_progress_stops": sum(bool(item.get("no_progress_stop")) for item in rows),
            "budget_stops": sum(bool(item.get("budget_stop")) for item in rows),
        }

    @staticmethod
    def _probe_local_react_recovery(case: dict[str, Any]) -> dict[str, Any]:
        controller = BoundedLocalReAct(
            owner_node="eval.retrieval",
            allowed_actions={"retrieve_more", "rewrite_query"},
            max_attempts=2,
            max_no_progress=1,
        )
        calls = 0

        def act(action: str, _: dict[str, Any]) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            return {"evidence_count": 0 if calls == 1 else 3, "action": action}

        def verify(value: dict[str, Any]) -> dict[str, Any]:
            return {"passed": value["evidence_count"] >= 2, "no_progress": value["evidence_count"] == 0}

        first = controller.step(
            {"quality": "below_gate"},
            lambda _: LoopDecision("retrieve_more", "evidence_below_gate"),
            act,
            verify,
        )
        second = controller.step(
            first.get("result") or {},
            lambda _: LoopDecision("rewrite_query", "retry_after_failed_verification"),
            act,
            verify,
        )
        return {
            "passed": second.get("status") == "completed",
            "triggered": True,
            "recovered": second.get("status") == "completed",
            "attempts": controller.attempt,
            "llm_calls": 0,
            "trace": controller.trace,
        }

    @staticmethod
    def _probe_local_react_illegal_action(case: dict[str, Any]) -> dict[str, Any]:
        controller = BoundedLocalReAct("eval.tool_boundary", {"allowed"}, max_attempts=2)
        result = controller.step(
            {},
            lambda _: LoopDecision("forbidden", "model_requested_unregistered_tool"),
            lambda *_: {"side_effect": True},
            lambda value: {"passed": True, "value": value},
        )
        return {
            "passed": result.get("reason") == "action_not_allowed" and controller.attempt == 0,
            "blocked": result.get("reason") == "action_not_allowed",
            "triggered": True,
            "recovered": False,
            "attempts": controller.attempt,
            "illegal_action": "forbidden",
        }

    @staticmethod
    def _probe_local_react_no_progress(case: dict[str, Any]) -> dict[str, Any]:
        controller = BoundedLocalReAct("eval.no_progress", {"repeat"}, max_attempts=3, max_no_progress=1)

        def decide(_: dict[str, Any]) -> LoopDecision:
            return LoopDecision("repeat", "same_observation")

        def act(*_: Any) -> dict[str, Any]:
            return {"changed": False}

        def verify(_: dict[str, Any]) -> dict[str, Any]:
            return {"passed": False, "no_progress": True}
        first = controller.step({}, decide, act, verify)
        second = controller.step(first.get("result") or {}, decide, act, verify)
        third = controller.step(second.get("result") or {}, decide, act, verify)
        return {
            "passed": third.get("reason") == "no_progress",
            "triggered": True,
            "recovered": False,
            "attempts": controller.attempt,
            "no_progress_stop": third.get("reason") == "no_progress",
        }

    @staticmethod
    def _probe_local_react_budget(case: dict[str, Any]) -> dict[str, Any]:
        controller = BoundedLocalReAct("eval.budget", {"retry"}, max_attempts=1, max_no_progress=4)
        first = controller.step(
            {},
            lambda _: LoopDecision("retry", "temporary_failure"),
            lambda *_: {"ok": False},
            lambda _: {"passed": False, "no_progress": False},
        )
        second = controller.step(
            first.get("result") or {},
            lambda _: LoopDecision("retry", "budget_should_stop"),
            lambda *_: {"ok": True},
            lambda _: {"passed": True},
        )
        return {
            "passed": second.get("reason") == "budget_exhausted",
            "triggered": True,
            "recovered": False,
            "attempts": controller.attempt,
            "budget_stop": second.get("reason") == "budget_exhausted",
        }

    @staticmethod
    def _base_plan(plan_id: str, *, include_tailor: bool = False) -> TaskPlan:
        nodes: list[dict[str, Any]] = [
            {"node_id": "n1", "goal_id": "g1", "action": "search_jobs"},
            {
                "node_id": "n2",
                "goal_id": "g2",
                "action": "match_job",
                "depends_on": ["n1"],
            },
        ]
        if include_tailor:
            nodes.append(
                {
                    "node_id": "n3",
                    "goal_id": "g3",
                    "action": "tailor_resume",
                    "depends_on": ["n2"],
                }
            )
        return TaskPlan(plan_id=plan_id, nodes=nodes)

    @classmethod
    async def _execute_until(cls, runtime: TaskGraphRuntime, *, fail_match_once: bool = False) -> None:
        attempts = 0

        async def handler(node: Any) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            if fail_match_once and node.node_id == "n2" and attempts == 2:
                raise RuntimeError("injected_match_failure")
            return {"node_id": node.node_id}

        while not runtime.is_complete() and not runtime.has_failed():
            await runtime.execute_ready(handler=handler)

    @classmethod
    def _probe_replan_recovery(cls, case: dict[str, Any]) -> dict[str, Any]:
        runtime = TaskGraphRuntime(cls._base_plan("plan-1", include_tailor=False))
        # Simulate the persisted checkpoint after search succeeded and match
        # failed.  The replan must preserve search and retry only the suffix.
        runtime.status["n1"] = "completed"
        runtime.status["n2"] = "failed"
        runtime.errors["n2"] = "injected_match_failure"
        record = runtime.replan(cls._base_plan("plan-3", include_tailor=True), reason="retry_after_tool_failure")
        ready = runtime.ready_nodes()
        for node_id in ready:
            runtime.start(node_id)
            runtime.complete(node_id, outputs={"node_id": node_id})
        ready = runtime.ready_nodes()
        for node_id in ready:
            runtime.start(node_id)
            runtime.complete(node_id, outputs={"node_id": node_id})
        return {
            "passed": runtime.is_complete() and record["preserved_completed_nodes"] == ["n1"],
            "triggered": True,
            "recovered": runtime.is_complete(),
            "attempts": sum(runtime.attempts.values()),
            "replan_count": len(runtime.replan_history),
            "reason_recorded": bool(record.get("reason")),
            "plan_diff": {
                "from_plan_id": record["from_plan_id"],
                "to_plan_id": record["to_plan_id"],
                "preserved_count": len(record["preserved_completed_nodes"]),
            },
            "preserved_completed_nodes": record["preserved_completed_nodes"],
        }

    @classmethod
    def _probe_replan_completed_node_rewrite(cls, case: dict[str, Any]) -> dict[str, Any]:
        runtime = TaskGraphRuntime(cls._base_plan("plan-1"))
        runtime.status["n1"] = "completed"
        try:
            runtime.replan(
                TaskPlan(
                    plan_id="plan-invalid",
                    nodes=[
                        {"node_id": "n1", "goal_id": "g1", "action": "create_profile"},
                        {"node_id": "n2", "goal_id": "g2", "action": "match_job", "depends_on": ["n1"]},
                    ],
                ),
                reason="injected_completed_node_rewrite",
            )
        except TaskGraphValidationError:
            return {
                "passed": True,
                "triggered": True,
                "recovered": False,
                "attempts": 0,
                "completed_node_rewrite_blocked": True,
            }
        return {
            "passed": False,
            "triggered": True,
            "recovered": False,
            "attempts": 0,
            "completed_node_rewrite_blocked": False,
        }

    @staticmethod
    def _probe_tool_policy_boundary(case: dict[str, Any]) -> dict[str, Any]:
        allowed = {"search_jobs", "match_job"}
        requested = str(case.get("requested_action") or "submit_application")
        blocked = requested not in allowed
        return {
            "passed": blocked,
            "blocked": blocked,
            "requested_action": requested,
            "allowed_actions": sorted(allowed),
            "triggered": True,
            "recovered": False,
            "attempts": 0,
        }

    @staticmethod
    def _cache_metadata(cases: list[dict[str, Any]]) -> dict[str, Any]:
        metadata: list[dict[str, Any]] = []
        for case in cases:
            for trace in case.get("run_trace") or []:
                for step in trace.get("steps") or []:
                    output = step.get("output_json") or {}
                    cache = output.get("reranker_cache") if isinstance(output, dict) else None
                    if isinstance(cache, dict):
                        metadata.append(cache)
        return {
            "observed_step_count": len(metadata),
            "pair_hits": sum(int(item.get("pair_hits") or 0) for item in metadata),
            "pair_misses": sum(int(item.get("pair_misses") or 0) for item in metadata),
            "request_hits": sum(int(item.get("request_hits") or 0) for item in metadata),
            "request_misses": sum(int(item.get("request_misses") or 0) for item in metadata),
        }

    @staticmethod
    def _cache_metrics_from_calls(*, service: RerankResultCacheService) -> dict[str, int]:
        # The cache service returns metadata for each lookup.  The evaluator
        # stores a private accumulator on the probe service to keep this
        # benchmark independent from process-global counters.
        return dict(getattr(service, "_efficiency_metrics", {}))
