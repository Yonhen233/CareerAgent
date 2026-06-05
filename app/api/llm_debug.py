from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import LLMCallLog
from app.models.schemas import LLMCallLogResponse

router = APIRouter(prefix="/llm/debug", tags=["llm-debug"])


@router.get("/logs", response_model=list[LLMCallLogResponse])
def list_llm_logs(limit: int = 50, db: Session = Depends(get_db)) -> list[LLMCallLogResponse]:
    limit = max(1, min(limit, 200))
    rows = db.query(LLMCallLog).order_by(LLMCallLog.created_at.desc()).limit(limit).all()
    return [LLMCallLogResponse.model_validate(row) for row in rows]
