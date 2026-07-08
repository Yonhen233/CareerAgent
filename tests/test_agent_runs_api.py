import asyncio

from app.api.agent_runs import create_background_agent_run
from app.core.security import AuthContext
from app.models.entities import AgentEvent
from app.models.schemas import AgentRunRequest


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
