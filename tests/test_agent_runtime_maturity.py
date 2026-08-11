import asyncio
import sqlite3

import pytest
from fastapi import HTTPException

from app.api.agent_runs import get_agent_events, get_agent_steps
from app.api.agent_governance import (
    create_agent_feedback,
    create_agent_memory,
    deactivate_agent_memory,
    list_agent_memories,
)
from app.agents.natural_language import NaturalLanguageAgentService
from app.core.config import Settings, get_settings
from app.core.llm import LLMCallBudget, LLMClient, LLMBudgetExceededError, llm_call_budget
from app.core.security import AuthContext
from app.models.entities import (
    AgentArtifact,
    AgentFeedback,
    AgentMemory,
    AgentQualityReview,
    AgentRun,
    ToolCircuitState,
)
from app.models.schemas import AgentFeedbackCreateRequest, AgentMemoryCreateRequest
from app.services.agent_runtime import (
    AgentErrorClassifier,
    AgentToolCircuitOpenError,
    AgentToolContractError,
    AgentToolRuntime,
)
from app.services.memory_feedback import AgentFeedbackService, CareerMemoryService
from app.services.online_quality import OnlineAgentQualityService
from app.core.redaction import SecurityRedactor
from app.services.trace_service import TraceService


def test_sqlite_lock_is_classified_as_retryable_dependency_failure():
    envelope = AgentErrorClassifier().classify(sqlite3.OperationalError("database is locked"))

    assert envelope.category == "dependency_transient"
    assert envelope.retryable is True
    assert envelope.recovery_action == "bounded_retry_then_dlq"


def test_runtime_rejects_wrong_input_type_before_tool_execution(db_session):
    runtime = AgentToolRuntime(settings=Settings())
    called = False

    async def handler():
        nonlocal called
        called = True
        return {"intent": "search_jobs"}

    with pytest.raises(AgentToolContractError, match="task_type expected str"):
        asyncio.run(
            runtime.execute(
                db_session,
                run_id=1,
                step_name="plan",
                tool_name="LangGraph.AgentPlanner",
                input_json={"task_type": 7},
                handler=handler,
                event_sink=lambda *_: None,
            )
        )
    assert called is False


def test_runtime_rejects_incomplete_planner_output_contract(db_session):
    runtime = AgentToolRuntime(settings=Settings())

    with pytest.raises(AgentToolContractError, match="executable steps"):
        asyncio.run(
            runtime.execute(
                db_session,
                run_id=1,
                step_name="plan",
                tool_name="LangGraph.AgentPlanner",
                input_json={"task_type": "full_career_flow"},
                handler=lambda: asyncio.sleep(0, result={"task_type": "full_career_flow"}),
                event_sink=lambda *_: None,
            )
        )


def test_runtime_rejects_invalid_high_risk_tool_outcome(db_session):
    runtime = AgentToolRuntime(settings=Settings())

    with pytest.raises(AgentToolContractError, match=r"status expected filled\|submitted"):
        runtime.execute_sync(
            db_session,
            step_name="browser_apply",
            tool_name="browser_apply",
            input_json={"url": "https://example.com", "fields": {}, "submit_selector": None},
            handler=lambda: {
                "status": "success",
                "final_url": "https://example.com/done",
                "filled_selectors": [],
            },
            event_sink=lambda *_: None,
        )


def test_nested_llm_budget_is_counted_by_parent_and_blocks_overrun():
    parent = LLMCallBudget("parent", max_calls=1, max_prompt_chars=100, max_completion_tokens=50)
    child = LLMCallBudget("child", max_calls=3, max_prompt_chars=300, max_completion_tokens=150)

    with llm_call_budget(parent):
        with llm_call_budget(child):
            child.reserve(trace_name="first", prompt_chars=10, max_tokens=10)
            with pytest.raises(LLMBudgetExceededError, match="parent"):
                child.reserve(trace_name="second", prompt_chars=10, max_tokens=10)
            child.record_usage(prompt_tokens=4, completion_tokens=3, total_tokens=7)

    assert parent.calls == 1
    assert child.calls == 1
    assert parent.actual_total_tokens == 7
    assert child.actual_total_tokens == 7


def test_runtime_retries_only_registered_idempotent_transient_tool(db_session):
    settings = Settings(
        agent_tool_retry_backoff_seconds=0,
        agent_tool_circuit_failure_threshold=10,
    )
    runtime = AgentToolRuntime(settings=settings)
    attempts = 0
    events = []

    async def handler():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("temporary source outage")
        return ([], {})

    result = asyncio.run(
        runtime.execute(
            db_session,
            run_id=1,
            step_name="search_jobs",
            tool_name="job_search.search_jobs",
            input_json={"query": "Agent", "location": None, "limit": 10},
            handler=handler,
            event_sink=lambda name, payload: events.append((name, payload)),
        )
    )

    assert result == ([], {})
    assert attempts == 2
    assert any(name == "tool_retry_scheduled" for name, _ in events)
    assert any(name == "tool_retry_recovered" for name, _ in events)


def test_non_idempotent_outbound_tool_is_never_auto_retried(db_session):
    runtime = AgentToolRuntime(settings=Settings(agent_tool_retry_backoff_seconds=0))
    attempts = 0

    def handler():
        nonlocal attempts
        attempts += 1
        raise ConnectionError("smtp disconnected")

    with pytest.raises(ConnectionError):
        runtime.execute_sync(
            db_session,
            step_name="approved_email_send",
            tool_name="email_send",
            input_json={"to": "a@example.com", "subject": "hello", "body": "body"},
            handler=handler,
            event_sink=lambda _name, _payload: None,
        )
    assert attempts == 1


def test_runtime_opens_persistent_circuit_after_repeated_dependency_failure(db_session):
    settings = Settings(
        agent_tool_retry_backoff_seconds=0,
        agent_tool_circuit_failure_threshold=2,
        agent_tool_circuit_cooldown_seconds=120,
    )
    runtime = AgentToolRuntime(settings=settings)

    async def fail():
        raise ConnectionError("source unavailable")

    with pytest.raises(ConnectionError):
        asyncio.run(
            runtime.execute(
                db_session,
                run_id=1,
                step_name="search_jobs",
                tool_name="job_search.search_jobs",
                input_json={"query": "Agent", "location": None, "limit": 10},
                handler=fail,
                event_sink=lambda _name, _payload: None,
            )
        )
    circuit = db_session.query(ToolCircuitState).one()
    assert circuit.status == "open"
    with pytest.raises(AgentToolCircuitOpenError):
        asyncio.run(
            runtime.execute(
                db_session,
                run_id=2,
                step_name="search_jobs",
                tool_name="job_search.search_jobs",
                input_json={"query": "Agent", "location": None, "limit": 10},
                handler=fail,
                event_sink=lambda _name, _payload: None,
            )
        )


def test_runtime_rejects_unregistered_tool_contract(db_session):
    runtime = AgentToolRuntime(settings=Settings(agent_strict_tool_contracts=True))

    async def handler():
        return {}

    with pytest.raises(AgentToolContractError):
        asyncio.run(
            runtime.execute(
                db_session,
                run_id=1,
                step_name="unknown",
                tool_name="shell.run_anything",
                input_json={},
                handler=handler,
                event_sink=lambda _name, _payload: None,
            )
        )


def test_typed_memory_supersedes_old_value_and_never_replays_raw_chat(db_session):
    service = CareerMemoryService(settings=Settings(agent_memory_context_max_chars=800))
    first = service.upsert(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        profile_id=None,
        memory_type="preference",
        memory_key="preferred_city",
        value_json={"city": "北京"},
    )
    second = service.upsert(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        profile_id=None,
        memory_type="preference",
        memory_key="preferred_city",
        value_json={"city": "深圳"},
    )
    db_session.refresh(first)
    context = service.compact_context(db_session, tenant_id="tenant-a", user_id="user-a")

    assert first.status == "superseded"
    assert second.status == "active"
    assert context["items"][0]["value"] == {"city": "深圳"}
    assert context["policy"] == "typed_facts_only_no_raw_chat_replay"


def test_typed_memory_does_not_cross_user_boundary_inside_same_tenant(db_session):
    service = CareerMemoryService()
    service.upsert(
        db_session,
        tenant_id="tenant-a",
        user_id="user-a",
        memory_type="constraint",
        memory_key="excluded_company",
        value_json={"company": "Company A"},
    )
    service.upsert(
        db_session,
        tenant_id="tenant-a",
        user_id="user-b",
        memory_type="constraint",
        memory_key="excluded_company",
        value_json={"company": "Company B"},
    )

    context = service.compact_context(db_session, tenant_id="tenant-a", user_id="user-a")

    assert [item["value"]["company"] for item in context["items"]] == ["Company A"]


def test_negative_feedback_creates_review_and_correction_memory(db_session):
    run = AgentRun(
        tenant_id="tenant-a",
        task_type="tailor_resume_for_job",
        profile_id=7,
        status="completed",
        input_json={},
        output_json={},
    )
    db_session.add(run)
    db_session.commit()
    row = AgentFeedbackService().record(
        db_session,
        run=run,
        tenant_id="tenant-a",
        user_id="user-a",
        verdict="incorrect",
        rating=1,
        reason_tags=["fabricated_claim"],
        comment="项目数据不是我的真实指标",
        correction_json={"forbidden_claim": "DAU 10 万"},
    )

    assert db_session.query(AgentFeedback).filter(AgentFeedback.id == row.id).one()
    assert db_session.query(AgentQualityReview).filter(AgentQualityReview.feedback_id == row.id).one()
    memory = db_session.query(AgentMemory).filter(AgentMemory.source_run_id == run.id).one()
    assert memory.memory_type == "correction"
    assert memory.value_json["forbidden_claim"] == "DAU 10 万"


def test_failed_run_is_routed_to_online_quality_review(db_session):
    run = AgentRun(
        tenant_id="tenant-a",
        task_type="tailor_resume_for_job",
        status="failed",
        input_json={},
        output_json={"error_envelope": {"category": "internal_error"}},
    )
    db_session.add(run)
    db_session.add(
        AgentArtifact(
            run=run,
            artifact_type="completion_verification",
            artifact_json={"passed": False},
        )
    )
    db_session.commit()

    report = OnlineAgentQualityService().assess_and_route(db_session, run=run)

    assert report["decision"] == "review_required"
    assert report["llm_judge_used"] is False
    assert db_session.query(AgentQualityReview).filter(AgentQualityReview.run_id == run.id).one()


def test_diagnostic_redaction_hides_secrets_and_pii_but_keeps_token_metrics():
    payload = {
        "authorization": "Bearer secret-value",
        "api_key": "sk-" + "1234567890abcdef",
        "prompt_tokens": 123,
        "message": "联系 liming@example.com 或 13800138000",
    }
    redacted = SecurityRedactor().redact(payload, redact_pii=True)

    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["prompt_tokens"] == 123
    assert "example.com" not in redacted["message"]
    assert "13800138000" not in redacted["message"]


def test_llm_prompt_preview_has_stable_fingerprint_and_redacted_pii():
    client = LLMClient()
    preview = client._prompt_preview(
        "system",
        "邮箱 liming@example.com，电话 13800138000",
        0.1,
        100,
        {"type": "json_object"},
    )

    assert len(preview["prompt_bundle_sha256"]) == 64
    assert preview["prompt_contract_version"] == "careeragent-prompt-observability-v2"
    assert "liming@example.com" not in preview["user_preview"]


def test_agent_child_resources_enforce_tenant_boundary(db_session, monkeypatch):
    monkeypatch.setenv("RBAC_ENABLED", "true")
    get_settings.cache_clear()
    run = AgentRun(
        tenant_id="tenant-a",
        user_id="user-b",
        task_type="find_jobs_for_profile",
        status="completed",
        input_json={},
        output_json={},
    )
    db_session.add(run)
    db_session.commit()
    auth = AuthContext("tenant-a", "user-a", set(), "session")

    with pytest.raises(HTTPException) as steps_error:
        get_agent_steps(run.id, db_session, auth)
    with pytest.raises(HTTPException) as events_error:
        get_agent_events(run.id, 0, 200, db_session, auth)

    assert steps_error.value.status_code == 404
    assert events_error.value.status_code == 404
    monkeypatch.delenv("RBAC_ENABLED", raising=False)
    get_settings.cache_clear()


def test_natural_language_repair_router_does_not_spend_llm_on_unrepairable_errors():
    service = NaturalLanguageAgentService()

    assert service._route_after_execute(
        {
            "execution_error": "LLM_API_KEY missing",
            "error_envelope": {"category": "configuration_error"},
        }
    ) == "finalize_failed"
    assert service._route_after_execute(
        {
            "execution_error": "tool circuit open",
            "error_envelope": {"category": "dependency_circuit_open"},
        }
    ) == "finalize_failed"
    assert service._route_after_execute(
        {
            "execution_error": "job id missing",
            "error_envelope": {"category": "input_or_state_validation"},
        }
    ) == "repair_user_plan"


def test_memory_and_feedback_governance_api_flow(db_session):
    auth = AuthContext("tenant-a", "user-a", set(), "session")
    memory = create_agent_memory(
        AgentMemoryCreateRequest(
            memory_type="preference",
            memory_key="preferred_city",
            value_json={"city": "深圳"},
        ),
        db_session,
        auth,
    )
    rows = list_agent_memories(None, 50, db_session, auth)
    assert [row.id for row in rows] == [memory.id]

    run = AgentRun(
        tenant_id="tenant-a",
        user_id="user-a",
        task_type="find_jobs_for_profile",
        status="completed",
        input_json={},
        output_json={},
    )
    db_session.add(run)
    db_session.commit()
    feedback = create_agent_feedback(
        run.id,
        AgentFeedbackCreateRequest(
            verdict="incomplete",
            rating=2,
            reason_tags=["missing_job_evidence"],
            correction_json={"expected": "展示 JD 引用"},
        ),
        db_session,
        auth,
    )
    assert feedback.run_id == run.id
    assert db_session.query(AgentQualityReview).filter(AgentQualityReview.feedback_id == feedback.id).one()

    inactive = deactivate_agent_memory(memory.id, db_session, auth)
    assert inactive.status == "inactive"
