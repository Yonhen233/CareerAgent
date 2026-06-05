from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import EvaluationRun
from app.models.schemas import EvaluationRunResponse
from app.services.evaluation_service import EvaluationService

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


@router.post("/run", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
async def run_evaluation(db: Session = Depends(get_db)) -> EvaluationRunResponse:
    run = await EvaluationService().run_sample_evaluation(db)
    return EvaluationRunResponse.model_validate(run)


@router.get("/results", response_model=list[EvaluationRunResponse])
def list_evaluation_runs(db: Session = Depends(get_db)) -> list[EvaluationRunResponse]:
    rows = db.query(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(50).all()
    return [EvaluationRunResponse.model_validate(row) for row in rows]
