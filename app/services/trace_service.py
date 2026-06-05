import time
from collections.abc import Callable
from typing import Any, Awaitable, TypeVar

from sqlalchemy.orm import Session

from app.models.entities import AgentArtifact, AgentRun, AgentStep

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
    ) -> AgentRun:
        run = AgentRun(
            task_type=task_type,
            profile_id=profile_id,
            job_id=job_id,
            status="running",
            input_json=input_json,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
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
        try:
            output = await handler()
            step.status = "completed"
            step.output_json = self._json_safe(output)
            step.latency_ms = int((time.perf_counter() - started) * 1000)
            db.commit()
            return output
        except Exception as exc:
            step.status = "failed"
            step.error_message = str(exc)
            step.latency_ms = int((time.perf_counter() - started) * 1000)
            db.commit()
            raise

    def add_artifact(self, db: Session, *, run_id: int, artifact_type: str, payload: dict[str, Any]) -> AgentArtifact:
        artifact = AgentArtifact(run_id=run_id, artifact_type=artifact_type, artifact_json=payload)
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        return artifact

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
        run.output_json = output_json
        run.error_message = error_message
        run.latency_ms = int((time.perf_counter() - started_at) * 1000)
        db.commit()
        db.refresh(run)
        return run

    def _json_safe(self, value: Any) -> Any:
        if hasattr(value, "id"):
            return {"id": getattr(value, "id")}
        if isinstance(value, list):
            return [self._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(k): self._json_safe(v) for k, v in value.items()}
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
