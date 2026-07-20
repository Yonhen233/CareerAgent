import time
import json
from collections.abc import Callable
from typing import Any, Awaitable, TypeVar

from sqlalchemy.orm import Session

from app.core.redis_client import RedisUnavailableError, get_redis_client, redis_key
from app.models.entities import AgentArtifact, AgentEvent, AgentRun, AgentStep

T = TypeVar("T")


class TraceService:
    def create_run(
        self,
        db: Session,
        *,
        task_type: str,
        input_json: dict[str, Any],
        profile_id: int | None = None,
        job_id: int | None = None,
        status: str = "running",
    ) -> AgentRun:
        run = AgentRun(
            task_type=task_type,
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
            payload={"task_type": task_type, "status": status, "profile_id": profile_id, "job_id": job_id},
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
            output = await handler()
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
            step.status = "failed"
            step.error_message = str(exc)
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
                    "error": str(exc),
                },
            )
            raise

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
        started_at: float,
    ) -> AgentRun:
        run.status = status
        run.error_message = error_message
        run.latency_ms = int((time.perf_counter() - started_at) * 1000)
        payload = dict(output_json or {})
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
        self.add_artifact(
            db,
            run_id=run.id,
            artifact_type="business_summary",
            payload=payload["business_summary"],
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
            return {str(k): self._json_safe(v) for k, v in value.items()}
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

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
