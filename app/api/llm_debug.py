from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import LLMCallLog
from app.models.schemas import LLMCallLogResponse

router = APIRouter(prefix="/llm/debug", tags=["llm-debug"])


@router.get("/logs", response_model=list[LLMCallLogResponse])
def list_llm_logs(
    limit: int = Query(default=50, ge=1, le=500),
    evaluation_run_id: int | None = Query(default=None),
    case_name: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[LLMCallLogResponse]:
    limit = max(1, min(limit, 500))
    scan_limit = 1000 if any([evaluation_run_id, case_name, stage]) else limit
    rows = db.query(LLMCallLog).order_by(LLMCallLog.created_at.desc()).limit(scan_limit).all()
    if evaluation_run_id is not None or case_name or stage:
        filtered = []
        for row in rows:
            context = row.context_json or {}
            if evaluation_run_id is not None and context.get("evaluation_run_id") != evaluation_run_id:
                continue
            if case_name and context.get("case_name") != case_name:
                continue
            if stage and context.get("stage") != stage:
                continue
            filtered.append(row)
        rows = filtered[:limit]
    return [LLMCallLogResponse.model_validate(row) for row in rows]
