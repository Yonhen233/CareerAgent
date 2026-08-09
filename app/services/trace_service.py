import time
import json
from collections.abc import Callable
from typing import Any, Awaitable, TypeVar

from sqlalchemy.orm import Session

from app.core.redis_client import RedisUnavailableError, get_redis_client, redis_key
from app.models.entities import AgentArtifact, AgentEvent, AgentRun, AgentStep
from app.core.config import get_settings
from app.services.agent_reliability import AgentExecutionBudgetExceeded
from app.services.agent_runtime import AgentErrorClassifier, AgentToolRuntime
from app.core.redaction import SecurityRedactor

T = TypeVar("T")


class TraceService:
    def __init__(self, *, runtime: AgentToolRuntime | None = None) -> None:
        self.runtime = runtime or AgentToolRuntime()
        self.error_classifier = AgentErrorClassifier()
        self.redactor = SecurityRedactor()

    def create_run(
        self,
        db: Session,
        *,
        task_type: str,
        input_json: dict[str, Any],
        profile_id: int | None = None,
        job_id: int | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
        status: str = "running",
    ) -> AgentRun:
        run = AgentRun(
            task_type=task_type,
            tenant_id=tenant_id,
            user_id=user_id,
            profile_id=profile_id,
            job_id=job_id,
            status=status,
            input_json=input_json,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        self.add_event(
            db,
            run_id=run.id,
            event_type="run_created",
            payload={
                "task_type": task_type,
                "status": status,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "profile_id": profile_id,
                "job_id": job_id,
            },
        )
        return run

    async def step(
        self,
        db: Session,
        *,
        run_id: int,
        step_name: str,
        tool_name: str,
        input_json: dict[str, Any] | None,
        handler: Callable[[], Awaitable[T]],
    ) -> T:
        self._enforce_execution_budget(
            db,
            run_id=run_id,
            tool_name=tool_name,
            input_json=input_json,
        )
        started = time.perf_counter()
        step = AgentStep(
            run_id=run_id,
            step_name=step_name,
            tool_name=tool_name,
            status="running",
            input_json=input_json,
        )
        db.add(step)
        db.commit()
        db.refresh(step)
        self.add_event(
            db,
            run_id=run_id,
            event_type="step_started",
            node_name=step_name,
            payload={"step_id": step.id, "tool_name": tool_name, "input_json": input_json or {}},
        )
        try:
            output = await self.runtime.execute(
                db,
                run_id=run_id,
                step_name=step_name,
                tool_name=tool_name,
                input_json=input_json,
                handler=handler,
                event_sink=lambda event_type, payload: self.add_event(
                    db,
                    run_id=run_id,
                    event_type=event_type,
                    node_name=step_name,
                    payload=payload,
                ),
            )
            step.status = "completed"
            step.output_json = self._json_safe(output)
            step.latency_ms = int((time.perf_counter() - started) * 1000)
            db.commit()
            self.add_event(
                db,
                run_id=run_id,
                event_type="step_completed",
                node_name=step_name,
                payload={
                    "step_id": step.id,
                    "tool_name": tool_name,
                    "latency_ms": step.latency_ms,
                    "output_json": step.output_json,
                },
            )
            return output
        except Exception as exc:
            db.rollback()
            envelope = self.error_classifier.classify(
                exc,
                tool_name=tool_name,
                step_name=step_name,
            )
            step.status = "failed"
            step.error_message = envelope.message
            step.latency_ms = int((time.perf_counter() - started) * 1000)
            db.commit()
            self.add_event(
                db,
                run_id=run_id,
                event_type="step_failed",
                node_name=step_name,
                payload={
                    "step_id": step.id,
                    "tool_name": tool_name,
                    "latency_ms": step.latency_ms,
                    "error": envelope.message,
                    "error_envelope": envelope.as_dict(),
                },
            )
            raise

    def _enforce_execution_budget(
        self,
        db: Session,
        *,
        run_id: int,
        tool_name: str,
        input_json: dict[str, Any] | None,
    ) -> None:
        settings = get_settings()
        steps = db.query(AgentStep).filter(AgentStep.run_id == run_id).all()
        if len(steps) >= settings.agent_max_tool_steps:
            payload = {
                "reason": "max_tool_steps_exceeded",
                "actual_steps": len(steps),
                "max_steps": settings.agent_max_tool_steps,
                "next_tool": tool_name,
            }
            self.add_event(db, run_id=run_id, event_type="execution_budget_rejected", payload=payload)
            raise AgentExecutionBudgetExceeded(
                f"Agent run {run_id} exceeded max_tool_steps={settings.agent_max_tool_steps}."
            )

        signature = json.dumps(input_json or {}, ensure_ascii=False, sort_keys=True, default=str)
        identical_count = sum(
            1
            for step in steps
            if step.tool_name == tool_name
            and json.dumps(step.input_json or {}, ensure_ascii=False, sort_keys=True, default=str) == signature
        )
        if identical_count >= settings.agent_max_identical_tool_calls:
            payload = {
                "reason": "repeated_tool_call_without_new_inputs",
                "tool_name": tool_name,
                "input_json": input_json or {},
                "previous_identical_calls": identical_count,
                "max_identical_calls": settings.agent_max_identical_tool_calls,
            }
            self.add_event(db, run_id=run_id, event_type="execution_budget_rejected", payload=payload)
            raise AgentExecutionBudgetExceeded(
                f"Agent run {run_id} repeated {tool_name} with identical inputs more than allowed."
            )

    def add_artifact(self, db: Session, *, run_id: int, artifact_type: str, payload: dict[str, Any]) -> AgentArtifact:
        artifact = AgentArtifact(run_id=run_id, artifact_type=artifact_type, artifact_json=payload)
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        self.add_event(
            db,
            run_id=run_id,
            event_type="artifact_created",
            node_name=artifact_type,
            payload={"artifact_id": artifact.id, "artifact_type": artifact_type, "artifact_json": payload},
        )
        return artifact

    def add_event(
        self,
        db: Session,
        *,
        run_id: int,
        event_type: str,
        payload: dict[str, Any],
        node_name: str | None = None,
    ) -> AgentEvent:
        event = AgentEvent(
            run_id=run_id,
            event_type=event_type,
            node_name=node_name,
            event_json=self._json_safe(payload),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        self._publish_event(event)
        return event

    def finish_run(
        self,
        db: Session,
        *,
        run: AgentRun,
        status: str,
        output_json: dict[str, Any] | None = None,
        error_message: str | None = None,
        error_exception: Exception | None = None,
        started_at: float,
    ) -> AgentRun:
        run.status = status
        run.error_message = error_message
        run.latency_ms = int((time.perf_counter() - started_at) * 1000)
        payload = dict(output_json or {})
        if error_exception is not None:
            payload["error_envelope"] = self.error_classifier.classify(error_exception).as_dict()
        from app.services.run_business_summary import RunBusinessSummaryService

        payload["business_summary"] = RunBusinessSummaryService().build(
            db,
            run=run,
            output_json=payload,
            status=status,
        )
        run.output_json = payload
        db.commit()
        db.refresh(run)
        from app.services.memory_feedback import CareerMemoryService
        from app.services.online_quality import OnlineAgentQualityService

        quality_report = OnlineAgentQualityService().assess_and_route(db, run=run)
        payload["runtime_quality"] = quality_report
        run.output_json = payload
        db.add(run)
        db.commit()
        db.refresh(run)
        learned_memories = CareerMemoryService().learn_run_episodes(db, run=run)
        self.add_artifact(
            db,
            run_id=run.id,
            artifact_type="business_summary",
            payload=payload["business_summary"],
        )
        self.add_artifact(
            db,
            run_id=run.id,
            artifact_type="runtime_quality",
            payload={
                **quality_report,
                "learned_memory_ids": [memory.id for memory in learned_memories],
            },
        )
        self.add_event(
            db,
            run_id=run.id,
            event_type="run_finished",
            payload={
                "status": status,
                "latency_ms": run.latency_ms,
                "error_message": error_message,
                "output_json": payload,
            },
        )
        return run

    def _json_safe(self, value: Any) -> Any:
        if hasattr(value, "id"):
            return {"id": getattr(value, "id")}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe(item) for item in value]
        if isinstance(value, dict):
            return self.redactor.redact({str(k): self._json_safe(v) for k, v in value.items()})
        if isinstance(value, (str, int, float, bool)) or value is None:
            return self.redactor.redact(value)
        return self.redactor.redact(str(value))

    def _publish_event(self, event: AgentEvent) -> None:
        try:
            redis = get_redis_client()
            redis.publish(
                redis_key("career_agent", "events", event.run_id),
                json.dumps(
                    {
                        "id": event.id,
                        "run_id": event.run_id,
                        "event_type": event.event_type,
                        "node_name": event.node_name,
                        "event_json": event.event_json,
                        "created_at": event.created_at.isoformat(),
                    },
                    ensure_ascii=False,
                ),
            )
        except RedisUnavailableError:
            return
