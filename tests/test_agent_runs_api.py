import asyncio
from types import SimpleNamespace

from app.api.agent_runs import create_background_agent_run, get_agent_steps
from app.core.security import AuthContext
from app.models.entities import AgentEvent, AgentRun, AgentStep
from app.models.schemas import AgentRunRequest
from app.services.trace_service import TraceService


def test_background_run_returns_failed_run_when_queue_unavailable(db_session, monkeypatch):
    class BrokenRunner:
        def enqueue_agent_run(self, run_id: int) -> None:
            raise RuntimeError("Redis task queue is required for background Agent runs. Set REDIS_ENABLED=true.")

    monkeypatch.setattr("app.api.agent_runs.get_task_runner", lambda: BrokenRunner())
    response = asyncio.run(
        create_background_agent_run(
            AgentRunRequest(task_type="find_jobs_for_profile", profile_id=123, query="Agent 开发实习生"),
            db=db_session,
            auth=AuthContext(tenant_id="default", user_id="pytest", roles=[], auth_type="pytest"),
        )
    )

    body = response.model_dump()
    assert body["id"] > 0
    assert body["status"] == "failed"
    assert body["output_json"]["error_type"] == "queue_unavailable"
    assert "Queue enqueue failed" in body["error_message"]

    events = db_session.query(AgentEvent).filter(AgentEvent.run_id == body["id"]).order_by(AgentEvent.id.asc()).all()
    assert [event.event_type for event in events] == ["run_created", "queue_enqueue_failed", "run_finished"]


def test_agent_steps_api_accepts_non_mapping_trace_outputs(db_session):
    run = AgentRun(task_type="full_career_flow", status="waiting_for_confirmation", input_json={})
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    db_session.add(
        AgentStep(
            run_id=run.id,
            step_name="search_jobs",
            tool_name="job_search.search_jobs",
            status="completed",
            input_json={"query": "Agent 开发实习"},
            output_json="legacy tuple repr",
        )
    )
    db_session.commit()

    rows = get_agent_steps(run.id, db=db_session)

    assert len(rows) == 1
    assert rows[0].output_json == "legacy tuple repr"


def test_trace_service_serializes_tuple_outputs_without_memory_addresses():
    output = TraceService()._json_safe(([SimpleNamespace(id=7)], {"source_errors": {}}))

    assert output == [[{"id": 7}], {"source_errors": {}}]
