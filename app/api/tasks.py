from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_admin
from app.models.schemas import TaskRunResponse
from app.services.task_queue import TaskQueueService
from app.services.task_runner import get_task_runner

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("/llm-workflow", response_model=TaskRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def enqueue_llm_workflow_task(
    case_limit: int | None = Query(default=None, ge=1, le=18),
    resume_from_last_completed: bool = Query(default=False),
    trace_path: str | None = Query(default=None),
    _: None = Depends(require_admin),
    db: Session = Depends(get_db),
) -> TaskRunResponse:
    service = TaskQueueService()
    task = service.create_llm_workflow_task(
        db,
        case_limit=case_limit,
        resume_from_last_completed=resume_from_last_completed,
        trace_path=trace_path,
    )
    try:
        get_task_runner().enqueue_task_run(task.id)
    except Exception as exc:  # noqa: BLE001
        task.status = "failed"
        task.error_message = f"Queue enqueue failed: {exc}"
        db.add(task)
        db.commit()
        raise HTTPException(status_code=503, detail=f"Task queue unavailable: {exc}") from exc
    return TaskRunResponse.model_validate(task)


@router.get("", response_model=list[TaskRunResponse])
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[TaskRunResponse]:
    rows = TaskQueueService().list_tasks(db, limit=limit)
    return [TaskRunResponse.model_validate(row) for row in rows]


@router.get("/{task_id}", response_model=TaskRunResponse)
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskRunResponse:
    task = TaskQueueService().get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return TaskRunResponse.model_validate(task)
