from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import AgentEvent, AgentRun
from app.services.trace_service import TraceService


ACTIVE_STATUSES = {"queued", "running", "waiting_for_confirmation"}


class StaleRunService:
    def __init__(self, trace: TraceService | None = None) -> None:
        self.settings = get_settings()
        self.trace = trace or TraceService()

    def find_stale(self, db: Session, *, threshold_minutes: int | None = None) -> list[dict[str, Any]]:
        threshold = datetime.now(timezone.utc) - timedelta(
            minutes=threshold_minutes or self.settings.agent_run_stale_after_minutes
        )
        rows = db.query(AgentRun).filter(AgentRun.status == "running").all()
        stale: list[dict[str, Any]] = []
        for run in rows:
            last_event = (
                db.query(AgentEvent)
                .filter(AgentEvent.run_id == run.id)
                .order_by(AgentEvent.created_at.desc())
                .first()
            )
            last_at = last_event.created_at if last_event else run.created_at
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=timezone.utc)
            if last_at < threshold:
                stale.append(
                    {
                        "run_id": run.id,
                        "task_type": run.task_type,
                        "profile_id": run.profile_id,
                        "job_id": run.job_id,
                        "last_event_id": last_event.id if last_event else None,
                        "last_event_type": last_event.event_type if last_event else None,
                        "last_stage": last_event.node_name if last_event else None,
                        "last_event_at": last_at.isoformat(),
                    }
                )
        return stale

    def mark_stale(self, db: Session, *, threshold_minutes: int | None = None) -> list[AgentRun]:
        stale_items = self.find_stale(db, threshold_minutes=threshold_minutes)
        marked: list[AgentRun] = []
        for item in stale_items:
            run = db.query(AgentRun).filter(AgentRun.id == item["run_id"]).first()
            if run is None or run.status != "running":
                continue
            output = dict(run.output_json or {})
            output.update(
                {
                    "error_type": "stale_run_timeout",
                    "last_event_id": item.get("last_event_id"),
                    "last_stage": item.get("last_stage"),
                    "last_event_at": item.get("last_event_at"),
                }
            )
            run.status = "failed"
            run.output_json = output
            run.error_message = "Agent run marked stale because no progress event was recorded before the threshold."
            db.add(run)
            db.commit()
            db.refresh(run)
            self.trace.add_event(
                db,
                run_id=run.id,
                event_type="run_marked_stale",
                payload=output,
                node_name=item.get("last_stage"),
            )
            marked.append(run)
        return marked
