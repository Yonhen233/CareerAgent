from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.local_react import BoundedLocalReAct, LoopDecision
from app.agents.task_graph import (
    GlobalPlanner,
    IntentValidator,
    TaskGraphRuntime,
    TaskGraphValidationError,
    TaskGraphValidator,
)
from app.agents.task_router import TaskRouteError, TaskRouter
from app.models.agent_plan_schemas import Goal, IntentEnvelope


DEFAULT_OUTPUT = ROOT / "evals" / "results" / "task_harness_eval.json"


def _run_case(name: str, capability: str, fn: Callable[[], Any], *, critical: bool = False) -> dict[str, Any]:
    try:
        value = fn()
        if asyncio.iscoroutine(value):
            value = asyncio.run(value)
        return {
            "name": name,
            "capability": capability,
            "critical": critical,
            "passed": True,
            "observed": value if isinstance(value, dict) else {"value": value},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "capability": capability,
            "critical": critical,
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _planner_dag() -> dict[str, Any]:
    intent = IntentEnvelope(
        goals=[
            Goal(goal_id="search", action="search_jobs"),
            Goal(goal_id="select", action="select_jobs", depends_on=["search"]),
            Goal(goal_id="tailor", action="tailor_resume", depends_on=["select"], for_each="selected_jobs"),
            Goal(goal_id="interview", action="prepare_interview", depends_on=["select"], for_each="selected_jobs"),
        ],
        missing_context=["profile", "job"],
    )
    plan = GlobalPlanner().compile(intent, plan_id="harness-plan")
    validation = TaskGraphValidator().validate(plan, intent=intent)
    assert validation.topological_order == ["n1", "n2", "n3", "n4"]
    assert plan.nodes[2].parallel_group == "selected_jobs"
    return {"topological_order": validation.topological_order, "ready_nodes": validation.ready_nodes}


def _intent_safety() -> dict[str, Any]:
    intent = IntentEnvelope(
        goals=[Goal(goal_id="apply", action="quick_apply")],
        forbidden_actions=["quick_apply"],
    )
    try:
        IntentValidator().validate(intent)
    except TaskGraphValidationError as exc:
        assert any("forbidden" in item for item in exc.errors)
        return {"rejected": True, "errors": exc.errors}
    raise AssertionError("forbidden action was accepted")


def _router_closed_world() -> dict[str, Any]:
    async def search(**_: Any) -> dict[str, Any]:
        return {"jobs": [1, 2]}

    async def run() -> dict[str, Any]:
        router = TaskRouter({"search_jobs": search})
        result = await router.dispatch("search_jobs")
        assert result["jobs"] == [1, 2]
        try:
            await router.dispatch("submit_application")
        except TaskRouteError:
            return {"known_route": True, "unknown_route_rejected": True}
        raise AssertionError("unregistered route was dispatched")

    return asyncio.run(run())


def _react_repair_then_success() -> dict[str, Any]:
    loop = BoundedLocalReAct("job_discovery.agentic_rag", {"rewrite_query", "stop"}, max_attempts=2)
    observations = [{"quality_passed": False}, {"quality_passed": False}]

    def decide(_: dict[str, Any]) -> LoopDecision:
        return LoopDecision("rewrite_query", "retrieval_quality_below_gate")

    def act(_: str, observation: dict[str, Any]) -> dict[str, Any]:
        return {"query": "Agent RAG backend", "candidate_count": 0 if loop.attempt == 1 else 4}

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        return {"passed": result["candidate_count"] >= 4}

    first = loop.step(observations[0], decide, act, verify)
    second = loop.step(observations[1], decide, act, verify)
    assert first["status"] == "continue"
    assert second["status"] == "completed"
    assert loop.attempt == 2
    return {"attempts": loop.attempt, "actions": loop.action_history, "final_status": second["status"]}


def _react_repeated_action_stops() -> dict[str, Any]:
    loop = BoundedLocalReAct("job_search", {"rewrite_query", "stop"}, max_attempts=4, max_no_progress=0)

    def act(action: str, _: dict[str, Any]) -> dict[str, Any]:
        return {"action": action, "changed": False}

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        return {"passed": False, "no_progress": True, **result}

    loop.step({"quality": "low"}, lambda _: LoopDecision("rewrite_query", "low_quality"), act, verify)
    stopped = loop.step({"quality": "low"}, lambda _: LoopDecision("rewrite_query", "still_low"), act, verify)
    assert stopped["status"] == "stopped"
    assert stopped["reason"] == "no_progress"
    return {"reason": stopped["reason"], "attempts": loop.attempt}


def _react_illegal_action_stops() -> dict[str, Any]:
    loop = BoundedLocalReAct("job_discovery.agentic_rag", {"rewrite_query"}, max_attempts=2)
    stopped = loop.step(
        {"quality": "low"},
        lambda _: LoopDecision("delete_all_jobs", "malicious_or_unknown"),
        lambda *_: {},
        lambda _: {"passed": True},
    )
    assert stopped["status"] == "stopped"
    assert stopped["reason"] == "action_not_allowed"
    return {"reason": stopped["reason"]}


def _react_budget_stops() -> dict[str, Any]:
    loop = BoundedLocalReAct("job_discovery.agentic_rag", {"rewrite_query"}, max_attempts=1)
    act = lambda action, _: {"action": action, "changed": True}
    verify = lambda result: {"passed": False, **result}
    loop.step({"quality": "low"}, lambda _: LoopDecision("rewrite_query", "low_quality"), act, verify)
    stopped = loop.step({"quality": "low"}, lambda _: LoopDecision("rewrite_query", "retry"), act, verify)
    assert stopped["status"] == "budget_exhausted"
    return {"reason": stopped["reason"], "attempts": loop.attempt}


def _replan_preserves_completed() -> dict[str, Any]:
    intent = IntentEnvelope(
        goals=[
            Goal(goal_id="search", action="search_jobs"),
            Goal(goal_id="match", action="match_job", depends_on=["search"]),
        ],
        missing_context=["profile"],
    )
    original = GlobalPlanner().compile(intent, plan_id="before")
    runtime = TaskGraphRuntime(original)

    async def run() -> None:
        await runtime.execute_ready(context={}, handler=lambda node: {"action": node.action})

    asyncio.run(run())
    replacement = original.model_copy(update={"plan_id": "after"})
    record = runtime.replan(replacement, reason="retrieval source timeout")
    assert record["preserved_completed_nodes"] == ["n1"]
    assert runtime.status["n1"] == "completed"
    assert runtime.status["n2"] == "pending"
    return {"preserved_completed_nodes": record["preserved_completed_nodes"], "status": runtime.as_dict()["status"]}


def _replan_rejects_completed_mutation() -> dict[str, Any]:
    intent = IntentEnvelope(goals=[Goal(goal_id="search", action="search_jobs")])
    original = GlobalPlanner().compile(intent, plan_id="before")
    runtime = TaskGraphRuntime(original)
    runtime.status["n1"] = "completed"
    mutated = original.model_copy(
        update={
            "plan_id": "after",
            "nodes": [original.nodes[0].model_copy(update={"action": "create_profile"})],
        }
    )
    try:
        runtime.replan(mutated, reason="unsafe planner output")
    except TaskGraphValidationError as exc:
        assert "cannot replace completed nodes" in str(exc)
        return {"rejected": True, "error": str(exc)}
    raise AssertionError("replan changed a completed node")


def _runtime_dependency_wave() -> dict[str, Any]:
    intent = IntentEnvelope(
        goals=[
            Goal(goal_id="search", action="search_jobs"),
            Goal(goal_id="tailor", action="tailor_resume", depends_on=["search"], for_each="jobs"),
            Goal(goal_id="interview", action="prepare_interview", depends_on=["search"], for_each="jobs"),
        ],
        missing_context=["profile", "job"],
    )
    runtime = TaskGraphRuntime(GlobalPlanner().compile(intent, plan_id="wave"))

    async def run() -> tuple[dict[str, Any], dict[str, Any]]:
        first = await runtime.execute_ready(context={}, handler=lambda node: {"action": node.action})
        second = await runtime.execute_ready(
            context={"profile_available": True, "job_available": True},
            handler=lambda node: {"action": node.action},
        )
        return first, second

    first, second = asyncio.run(run())
    assert first["state"]["completed_nodes"] == ["n1"]
    assert set(second["state"]["completed_nodes"]) == {"n1", "n2", "n3"}
    return {"first_wave": first["state"]["completed_nodes"], "final_status": second["state"]["status"]}


def evaluate() -> dict[str, Any]:
    cases = [
        _run_case("planner_compiles_dependency_dag", "intent_to_task_graph", _planner_dag),
        _run_case("intent_forbidden_action_is_rejected", "intent_to_task_graph", _intent_safety, critical=True),
        _run_case("router_is_closed_world", "tool_routing", _router_closed_world, critical=True),
        _run_case("agentic_rag_repairs_once_then_completes", "local_react", _react_repair_then_success),
        _run_case("local_react_stops_repeated_no_progress", "local_react", _react_repeated_action_stops, critical=True),
        _run_case("local_react_rejects_illegal_action", "local_react", _react_illegal_action_stops, critical=True),
        _run_case("local_react_obeys_attempt_budget", "local_react", _react_budget_stops, critical=True),
        _run_case("replan_preserves_completed_nodes", "replan", _replan_preserves_completed, critical=True),
        _run_case("replan_rejects_completed_node_mutation", "replan", _replan_rejects_completed_mutation, critical=True),
        _run_case("runtime_executes_dependency_safe_wave", "runtime", _runtime_dependency_wave),
    ]
    by_capability: dict[str, dict[str, Any]] = {}
    for capability in sorted({case["capability"] for case in cases}):
        subset = [case for case in cases if case["capability"] == capability]
        by_capability[capability] = {
            "case_count": len(subset),
            "passed_count": sum(case["passed"] for case in subset),
            "pass_rate": round(sum(case["passed"] for case in subset) / len(subset), 4),
        }
    critical = [case for case in cases if case["critical"]]
    summary = {
        "case_count": len(cases),
        "passed_count": sum(case["passed"] for case in cases),
        "pass_rate": round(sum(case["passed"] for case in cases) / len(cases), 4),
        "critical_case_count": len(critical),
        "critical_pass_rate": round(sum(case["passed"] for case in critical) / len(critical), 4),
        "release_gate_passed": all(case["passed"] for case in cases),
        "by_capability": by_capability,
        "external_llm_calls": 0,
        "note": "离线 Harness 控制器评测；不代表 LLM 的自然语言意图识别准确率或真实 API 生成质量。",
    }
    return {
        "evaluation_type": "task_harness_offline",
        "methodology_version": "task-harness-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline TaskGraph/ReAct/Replan/Agentic-RAG harness evaluation.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0 if result["summary"]["release_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
