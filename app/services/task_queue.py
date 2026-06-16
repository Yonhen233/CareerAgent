from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.entities import EvaluationRun, TaskRun, utc_now
from app.services.evaluation_service import EvaluationService


def _progress_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    case_count = int(summary.get("case_count") or 0)
    completed = int(summary.get("completed_cases") or 0)
    percent = round(completed / case_count, 4) if case_count else 0.0
    return {
        "case_count": case_count,
        "completed_cases": completed,
        "remaining_cases": int(summary.get("remaining_cases") or max(case_count - completed, 0)),
        "percent": percent,
        "current_case": summary.get("current_case"),
        "evaluation_status": summary.get("status"),
        "end_to_end_pass_rate": summary.get("end_to_end_pass_rate"),
        "fit_label_accuracy": summary.get("fit_label_accuracy"),
        "tailor_pass_rate": summary.get("tailor_pass_rate"),
    }


class TaskQueueService:
    def create_llm_workflow_task(
        self,
        db: Session,
        *,
        case_limit: int | None,
        resume_from_last_completed: bool,
        trace_path: str | None,
    ) -> TaskRun:
        payload = {
            "case_limit": case_limit,
            "resume_from_last_completed": resume_from_last_completed,
            "trace_path": trace_path or "data/runtime/llm_workflow_trace_latest.jsonl",
        }
        task = TaskRun(
            task_type="llm_workflow",
            status="queued",
            input_json=payload,
            progress_json={"case_count": case_limit or 18, "completed_cases": 0, "remaining_cases": case_limit or 18},
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    async def run_llm_workflow_task(self, task_id: int) -> None:
        db = SessionLocal()
        try:
            task = db.query(TaskRun).filter(TaskRun.id == task_id).first()
            if task is None:
                return
            task.status = "running"
            task.started_at = utc_now()
            task.progress_json = {**(task.progress_json or {}), "status": "running", "percent": 0.0}
            db.add(task)
            db.commit()
            db.refresh(task)

            params = task.input_json or {}

            def on_progress(run: EvaluationRun) -> None:
                self.update_progress(task_id, run.summary_json, evaluation_run_id=run.id)

            run = await EvaluationService().run_llm_workflow_evaluation(
                db,
                case_limit=params.get("case_limit"),
                resume_from_last_completed=bool(params.get("resume_from_last_completed")),
                trace_path=Path(str(params.get("trace_path") or "data/runtime/llm_workflow_trace_latest.jsonl")),
                progress_callback=on_progress,
            )
            task = db.query(TaskRun).filter(TaskRun.id == task_id).first()
            if task is None:
                return
            task.status = "completed" if run.summary_json.get("status") == "completed" else str(run.summary_json.get("status"))
            task.progress_json = {
                **_progress_from_summary(run.summary_json),
                "status": task.status,
                "evaluation_run_id": run.id,
            }
            task.output_json = {"evaluation_run_id": run.id, "summary": run.summary_json}
            task.completed_at = utc_now()
            db.add(task)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            task = db.query(TaskRun).filter(TaskRun.id == task_id).first()
            if task is not None:
                task.status = "failed"
                task.error_message = f"{exc.__class__.__name__}: {str(exc) or repr(exc)}"
                task.completed_at = utc_now()
                task.progress_json = {**(task.progress_json or {}), "status": "failed"}
                db.add(task)
                db.commit()
        finally:
            db.close()

    def update_progress(
        self,
        task_id: int,
        summary: dict[str, Any],
        *,
        evaluation_run_id: int | None = None,
        db: Session | None = None,
    ) -> None:
        owns_session = db is None
        db = db or SessionLocal()
        try:
            task = db.query(TaskRun).filter(TaskRun.id == task_id).first()
            if task is None:
                return
            task.progress_json = {
                **_progress_from_summary(summary),
                "status": "running",
                "evaluation_run_id": evaluation_run_id,
            }
            db.add(task)
            db.commit()
        finally:
            if owns_session:
                db.close()

    def list_tasks(self, db: Session, *, limit: int = 50) -> list[TaskRun]:
        return db.query(TaskRun).order_by(TaskRun.created_at.desc()).limit(limit).all()

    def get_task(self, db: Session, task_id: int) -> TaskRun | None:
        return db.query(TaskRun).filter(TaskRun.id == task_id).first()
