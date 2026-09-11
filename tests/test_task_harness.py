import asyncio

import pytest

from app.agents.local_react import BoundedLocalReAct, LoopDecision
from app.agents.task_graph import GlobalPlanner, IntentValidator, TaskGraphRuntime, TaskGraphValidationError, TaskGraphValidator
from app.agents.task_router import TaskRouteError, TaskRouter
from app.models.agent_plan_schemas import Goal, IntentEnvelope


def test_global_planner_compiles_dependency_dag_and_ready_nodes():
    intent = IntentEnvelope(
        goals=[
            Goal(goal_id="g1", action="search_jobs"),
            Goal(goal_id="g2", action="select_jobs", depends_on=["g1"]),
            Goal(goal_id="g3", action="tailor_resume", depends_on=["g2"], for_each="selected_jobs"),
            Goal(goal_id="g4", action="prepare_interview", depends_on=["g2"], for_each="selected_jobs"),
        ],
        missing_context=["profile", "job"],
    )

    plan = GlobalPlanner().compile(intent, plan_id="plan-test")
    validation = TaskGraphValidator().validate(plan, intent=intent)

    assert validation.topological_order == ["n1", "n2", "n3", "n4"]
    assert validation.ready_nodes == ["n1"]
    assert runtime_batches(plan) == [["n1"]]
    assert plan.nodes[2].parallel_group == "selected_jobs"
    assert plan.nodes[3].depends_on == ["n2"]


def test_task_graph_rejects_cycle_and_forbidden_action():
    intent = IntentEnvelope(
        goals=[
            Goal(goal_id="g1", action="search_jobs", depends_on=["g2"]),
            Goal(goal_id="g2", action="select_jobs", depends_on=["g1"]),
        ],
        forbidden_actions=["search_jobs"],
    )

    with pytest.raises(TaskGraphValidationError):
        IntentValidator().validate(intent, available_context={})


def test_local_react_blocks_unregistered_and_repeated_actions():
    loop = BoundedLocalReAct("job_search", {"rewrite_query", "stop"}, max_attempts=3, max_no_progress=0)

    def act(action, _observation):
        return {"action": action, "changed": False}

    first = loop.step(
        {"quality": "low"},
        lambda _: LoopDecision("rewrite_query", "insufficient_results"),
        act,
        lambda result: {"passed": False, "no_progress": True, **result},
    )
    second = loop.step(
        {"quality": "low"},
        lambda _: LoopDecision("rewrite_query", "still_insufficient"),
        act,
        lambda result: {"passed": False, "no_progress": True, **result},
    )

    assert first["status"] == "continue"
    assert second["status"] == "stopped"
    assert second["reason"] == "no_progress"


def test_task_router_is_closed_world_and_supports_async_handlers():
    async def search(**_kwargs):
        return {"jobs": [1]}

    router = TaskRouter({"search_jobs": search})
    assert asyncio.run(router.dispatch("search_jobs"))["jobs"] == [1]
    with pytest.raises(TaskRouteError):
        asyncio.run(router.dispatch("quick_apply"))


def test_local_react_async_action_is_bounded_and_verifiable():
    loop = BoundedLocalReAct("job_discovery.agentic_rag", {"rewrite_query"}, max_attempts=1)

    async def act(_action, _observation):
        return {"query": "Agent workflow", "candidate_count": 3}

    async def run():
        return await loop.astep(
            {"quality_passed": False},
            lambda _: LoopDecision("rewrite_query", "retrieval_quality_below_gate"),
            act,
            lambda result: {"passed": result["candidate_count"] > 0},
        )

    result = asyncio.run(run())
    assert result["status"] == "completed"
    assert result["attempt"] == 1
    assert loop.state()["executed_actions"] == ["rewrite_query"]


def test_task_graph_replan_preserves_completed_nodes():
    intent = IntentEnvelope(
        goals=[
            Goal(goal_id="g1", action="search_jobs"),
            Goal(goal_id="g2", action="match_job", depends_on=["g1"]),
        ],
        missing_context=["profile"],
    )
    plan = GlobalPlanner().compile(intent, plan_id="plan-before-replan")
    runtime = TaskGraphRuntime(plan)

    async def run():
        await runtime.execute_ready(
            context={},
            handler=lambda node: {"action": node.action},
        )

    asyncio.run(run())
    replacement = plan.model_copy(update={"plan_id": "plan-after-replan"})
    record = runtime.replan(replacement, reason="source timeout")
    assert record["preserved_completed_nodes"] == ["n1"]
    assert runtime.status["n1"] == "completed"
    assert runtime.status["n2"] == "pending"
    assert runtime.as_dict()["replan_history"][0]["to_plan_id"] == "plan-after-replan"


def test_task_graph_runtime_executes_dependency_safe_batches_and_can_resume():
    intent = IntentEnvelope(
        goals=[
            Goal(goal_id="g1", action="search_jobs"),
            Goal(goal_id="g2", action="tailor_resume", depends_on=["g1"], for_each="jobs"),
            Goal(goal_id="g3", action="prepare_interview", depends_on=["g1"], for_each="jobs"),
        ],
        missing_context=["profile", "job"],
    )
    plan = GlobalPlanner().compile(intent, plan_id="plan-runtime")
    runtime = TaskGraphRuntime(plan)

    async def run():
        first = await runtime.execute_ready(
            context={"search_results_available": True},
            handler=lambda node: {"action": node.action},
        )
        second = await runtime.execute_ready(
            context={"profile_available": True, "job_available": True},
            handler=lambda node: {"action": node.action},
        )
        return first, second

    first, second = asyncio.run(run())
    assert first["status"] == "completed_batch"
    assert first["state"]["completed_nodes"] == ["n1"]
    assert set(second["state"]["completed_nodes"]) == {"n1", "n2", "n3"}


def runtime_batches(plan):
    from app.agents.task_graph import TaskGraphRuntime

    runtime = TaskGraphRuntime(plan)
    return runtime.ready_batches()
