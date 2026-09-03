from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agents.natural_language import NaturalLanguageAgentService
from app.models.entities import AgentRun
from app.models.schemas import NaturalLanguageAgentRequest, TaskState
from app.services.context_recovery import ContextRecoveryService
from app.services.context_runtime import ContextIntegrityError
from app.services.conversation_compactor import ConversationCompactor
from app.services.task_state import TaskStateReducer, TaskStateValidationError
from app.services.token_optimization import PromptSectionProfiler


def _long_messages() -> list[dict]:
    return [
        {
            "message_id": f"m{index}",
            "role": "user" if index % 2 else "assistant",
            "content": f"第 {index} 条历史讨论。" * 40,
        }
        for index in range(1, 11)
    ]


class FixedEstimator:
    def count(self, value):
        if isinstance(value, list):
            return SimpleNamespace(tokens=900 if len(value) > 6 else 300)
        return SimpleNamespace(tokens=180)


class CompactorLLM:
    available = True

    def __init__(self, mode: str = "valid") -> None:
        self.mode = mode
        self.calls = 0

    async def generate_json(self, **kwargs):
        self.calls += 1
        payload = json.loads(kwargs["user_prompt"])
        messages = payload["messages"]
        state = payload["current_task_state"]
        ids = [item["message_id"] for item in messages]
        if self.mode == "missing_ids":
            ids = ids[:-1]
        discussion = "此前地点是北京，后来改为深圳。"
        if self.mode == "old_active":
            discussion = "当前地点是北京。"
        return {
            "discussion_summary": discussion,
            "rationales": ["优先匹配 Agent 岗位"],
            "unresolved_questions": [],
            "source_message_ids": ids,
            "task_state_version": state["version"],
            "task_state_claims": {
                "target_role": state["target_role"],
                "location": "北京" if self.mode == "old_active" else state["location"],
                "forbidden_actions": state["forbidden_actions"],
                "completed_actions": state["completed_actions"],
            },
            "historical_changes": [
                {"field": "location", "old_value": "北京", "new_value": "深圳"}
            ],
            "authoritative": False,
        }


class PlannerLLM:
    available = True

    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid
        self.calls = 0

    async def generate_json(self, **kwargs):
        self.calls += 1
        state_updates = {
            "target_role": {"operation": "set", "value": "RAG Agent"},
            "location": {"operation": "set", "value": "深圳"},
            "forbidden_actions_to_add": ["auto_apply"],
            "selected_actions_to_add": ["search_jobs"],
        }
        if self.invalid:
            state_updates = {"pending_actions_to_add": ["delete_database"]}
        return {
            "intent": "search_jobs",
            "query": "RAG Agent",
            "profile": None,
            "job": None,
            "needs_profile": False,
            "needs_job": False,
            "actions": ["search_jobs"],
            "state_updates": state_updates,
            "reason": "按当前偏好搜索岗位。",
        }


def test_task_state_sets_role_location_and_forbidden_action_in_one_turn():
    state = TaskStateReducer().merge(
        None,
        {
            "target_role": {"operation": "set", "value": "RAG Agent"},
            "location": {"operation": "set", "value": "深圳"},
            "selected_actions_to_add": ["search_jobs"],
        },
        source_message_id="m17",
        source_role="user",
        source_text="只看深圳的 RAG Agent 岗位，不要自动投递。",
    )
    assert state.target_role == "RAG Agent"
    assert state.location == "深圳"
    assert state.forbidden_actions == ["auto_apply"]
    assert state.provenance["location"] == "m17"


def test_task_state_correction_invalidates_old_location():
    reducer = TaskStateReducer()
    beijing = reducer.merge(
        None,
        {"location": {"operation": "set", "value": "北京"}},
        source_message_id="m1",
        source_role="user",
        source_text="只看北京。",
    )
    shenzhen = reducer.merge(
        beijing,
        {"location": {"operation": "set", "value": "深圳"}},
        source_message_id="m2",
        source_role="user",
        source_text="地点从北京改成深圳。",
    )
    assert shenzhen.location == "深圳"
    assert shenzhen.corrections[-1].old_value == "北京"
    assert shenzhen.corrections[-1].new_value == "深圳"
    assert shenzhen.corrections[-1].source_message_id == "m2"


def test_omitted_location_does_not_clear_existing_state():
    current = TaskState(version=3, target_role="Agent", location="深圳")
    result = TaskStateReducer().merge(
        current,
        {"constraints_to_add": ["只看实习"]},
        source_message_id="m3",
        source_role="user",
        source_text="只看实习岗位。",
    )
    assert result.location == "深圳"


def test_repeated_constraint_does_not_mutate_provenance_without_version_change():
    current = TaskState(
        version=3,
        constraints=["只看实习"],
        provenance={"constraints.只看实习": "m1"},
    )
    result = TaskStateReducer().merge(
        current,
        {"constraints_to_add": ["只看实习"]},
        source_message_id="m2",
        source_role="user",
        source_text="仍然只看实习。",
    )
    assert result.version == 3
    assert result.provenance["constraints.只看实习"] == "m1"


def test_deterministic_guard_adds_no_auto_apply_when_planner_omits_it():
    result = TaskStateReducer().merge(
        None,
        {},
        source_message_id="m4",
        source_role="user",
        source_text="可以生成材料，但不要自动投递。",
    )
    assert "auto_apply" in result.forbidden_actions


def test_deterministic_guard_understands_confirmation_before_external_send():
    result = TaskStateReducer().merge(
        TaskState(forbidden_actions=["auto_apply"]),
        {"forbidden_actions_to_remove": ["auto_apply"]},
        source_message_id="m4b",
        source_role="user",
        source_text="现在允许自动投递，但真正外发前仍要确认。",
    )
    assert "auto_apply" not in result.forbidden_actions
    assert "unapproved_high_risk_action" in result.forbidden_actions


def test_non_user_content_cannot_update_task_state():
    with pytest.raises(TaskStateValidationError, match="Only the current user"):
        TaskStateReducer().merge(
            None,
            {"location": {"operation": "set", "value": "上海"}},
            source_message_id="a1",
            source_role="assistant",
            source_text="建议改到上海。",
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"pending_actions_to_add": ["delete_database"]},
        {"location": {"operation": "append", "value": "深圳"}},
        {"unknown_field": "value"},
    ],
)
def test_unknown_actions_operations_and_fields_are_rejected(updates):
    with pytest.raises(TaskStateValidationError):
        TaskStateReducer.validate_updates(updates)


def test_removing_forbidden_action_requires_current_explicit_permission():
    current = TaskState(forbidden_actions=["auto_apply"])
    with pytest.raises(TaskStateValidationError, match="explicit permission"):
        TaskStateReducer().merge(
            current,
            {"forbidden_actions_to_remove": ["auto_apply"]},
            source_message_id="m5",
            source_role="user",
            source_text="继续搜索岗位。",
        )
    allowed = TaskStateReducer().merge(
        current,
        {"forbidden_actions_to_remove": ["auto_apply"]},
        source_message_id="m6",
        source_role="user",
        source_text="现在允许自动投递。",
    )
    assert "auto_apply" not in allowed.forbidden_actions


def test_invalid_planner_state_update_repairs_once_and_keeps_old_state(db_session):
    llm = PlannerLLM(invalid=True)
    service = NaturalLanguageAgentService(llm=llm)
    old_state = TaskState(version=4, location="深圳", forbidden_actions=["auto_apply"])
    request = NaturalLanguageAgentRequest(
        instruction="继续搜索 Agent 岗位。",
        task_state=old_state,
    )
    with pytest.raises(ValueError, match="计划契约校验失败"):
        asyncio.run(service._build_plan(db_session, request))
    assert llm.calls == 2
    assert request.task_state == old_state


def test_recent_three_turns_are_kept_without_compactor_call(db_session):
    llm = CompactorLLM()
    result = asyncio.run(
        ConversationCompactor(llm).compact_if_needed(
            db_session,
            run_id=None,
            messages=_long_messages()[-6:],
            node_budget_tokens=1000,
            task_state=TaskState(location="深圳"),
        )
    )
    assert llm.calls == 0
    assert [item["message_id"] for item in result.recent_messages] == [
        item["message_id"] for item in _long_messages()[-6:]
    ]
    assert [item["content"] for item in result.recent_messages] == [
        item["content"] for item in _long_messages()[-6:]
    ]
    assert result.compactor_called is False


def test_long_history_triggers_exactly_one_successful_compactor_call(db_session):
    llm = CompactorLLM()
    compactor = ConversationCompactor(llm)
    compactor.estimator = FixedEstimator()
    result = asyncio.run(
        compactor.compact_if_needed(
            db_session,
            run_id=None,
            messages=_long_messages(),
            node_budget_tokens=1000,
            task_state=TaskState(location="深圳"),
        )
    )
    assert llm.calls == 1
    assert result.compactor_attempts == 1
    assert len(result.recent_messages) == 6


def test_missing_summary_message_ids_retries_then_falls_back_to_raw(db_session):
    llm = CompactorLLM("missing_ids")
    compactor = ConversationCompactor(llm)
    compactor.estimator = FixedEstimator()
    result = asyncio.run(
        compactor.compact_if_needed(
            db_session,
            run_id=None,
            messages=_long_messages(),
            node_budget_tokens=1000,
            task_state=TaskState(location="深圳"),
        )
    )
    assert llm.calls == 2
    assert result.fallback_to_raw is True
    assert len(result.recent_messages) == 10
    assert "source_message_ids" in result.validation_errors[-1]


def test_summary_cannot_reactivate_corrected_old_location(db_session):
    task_state = TaskState(
        version=3,
        location="深圳",
        corrections=[
            {
                "field": "location",
                "old_value": "北京",
                "new_value": "深圳",
                "source_message_id": "m2",
            }
        ],
    )
    llm = CompactorLLM("old_active")
    compactor = ConversationCompactor(llm)
    compactor.estimator = FixedEstimator()
    result = asyncio.run(
        compactor.compact_if_needed(
            db_session,
            run_id=None,
            messages=_long_messages(),
            node_budget_tokens=1000,
            task_state=task_state,
        )
    )
    assert result.fallback_to_raw is True
    assert any("conflicts" in error or "old value" in error for error in result.validation_errors)


def test_compactor_failure_stops_when_raw_history_exceeds_budget(db_session):
    llm = CompactorLLM("missing_ids")
    compactor = ConversationCompactor(llm)
    compactor.estimator = FixedEstimator()
    with pytest.raises(ContextIntegrityError, match="failed twice"):
        asyncio.run(
            compactor.compact_if_needed(
                db_session,
                run_id=None,
                messages=_long_messages(),
                node_budget_tokens=800,
                task_state=TaskState(location="深圳"),
            )
        )


def test_checkpoint_recovery_preserves_task_state_exactly(db_session):
    run = AgentRun(
        tenant_id="tenant-a",
        user_id="user-a",
        task_type="full_career_flow",
        status="running",
        input_json={},
    )
    db_session.add(run)
    db_session.commit()
    task_state = TaskState(
        version=4,
        target_role="RAG Agent",
        location="深圳",
        forbidden_actions=["auto_apply"],
        pending_actions=["tailor_resume"],
    )
    recovered = ContextRecoveryService().rebuild_for_next_node(
        db_session,
        run=run,
        state={"task_state": task_state.model_dump(), "context_refs": {"task_state_version": 4}},
        next_node="completion_gate",
    )
    assert recovered.task_state == task_state.model_dump()


def test_rewind_uses_historical_checkpoint_task_state_not_latest_branch(db_session):
    run = AgentRun(
        tenant_id="tenant-a",
        user_id="user-a",
        task_type="full_career_flow",
        status="running",
        input_json={},
    )
    db_session.add(run)
    db_session.commit()
    historical = TaskState(version=2, location="北京")
    latest = TaskState(
        version=3,
        location="深圳",
        corrections=[
            {
                "field": "location",
                "old_value": "北京",
                "new_value": "深圳",
                "source_message_id": "m2",
            }
        ],
    )
    historical_recovery = ContextRecoveryService().rebuild_for_next_node(
        db_session,
        run=run,
        state={"task_state": historical.model_dump()},
        next_node="completion_gate",
    )
    assert historical_recovery.task_state["location"] == "北京"
    assert historical_recovery.task_state != latest.model_dump()


def test_planner_returns_plan_and_state_updates_in_one_business_call(db_session):
    llm = PlannerLLM()
    service = NaturalLanguageAgentService(llm=llm)
    plan = asyncio.run(
        service._build_plan(
            db_session,
            NaturalLanguageAgentRequest(
                instruction="搜索深圳 RAG Agent 岗位，不要自动投递。",
                task_state=TaskState(),
            ),
        )
    )
    assert llm.calls == 1
    assert plan["state_updates"]["location"]["value"] == "深圳"
    assert plan["state_updates"]["forbidden_actions_to_add"] == ["auto_apply"]


def test_compactor_uses_registered_prompt_section_names(db_session):
    class Recorder:
        available = True

        def __init__(self):
            self.sections = {}

        async def generate_json(self, **kwargs):
            self.sections = kwargs["prompt_sections"]
            return {}

    recorder = Recorder()
    compactor = ConversationCompactor(recorder)
    asyncio.run(
        compactor._generate_summary(
            db_session,
            run_id=None,
            older=_long_messages()[:4],
            task_state=TaskState(),
            attempt=1,
            previous_error=None,
        )
    )
    assert set(recorder.sections) <= set(PromptSectionProfiler.SECTION_NAMES)
