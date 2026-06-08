from fastapi import APIRouter, Depends, Query, status
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


@router.post("/pdf-chunk-strategies", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
def run_pdf_chunk_strategy_evaluation(db: Session = Depends(get_db)) -> EvaluationRunResponse:
    run = EvaluationService().run_pdf_chunk_strategy_evaluation(db)
    return EvaluationRunResponse.model_validate(run)


@router.post("/rag-strategies", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
def run_rag_strategy_evaluation(db: Session = Depends(get_db)) -> EvaluationRunResponse:
    run = EvaluationService().run_rag_strategy_evaluation(db)
    return EvaluationRunResponse.model_validate(run)


@router.post("/agent-full-flow", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
async def run_agent_full_flow_evaluation(db: Session = Depends(get_db)) -> EvaluationRunResponse:
    run = await EvaluationService().run_agent_full_flow_evaluation(db)
    return EvaluationRunResponse.model_validate(run)


@router.post("/jd-parser", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
async def run_jd_parser_evaluation(db: Session = Depends(get_db)) -> EvaluationRunResponse:
    run = await EvaluationService().run_jd_parser_evaluation(db)
    return EvaluationRunResponse.model_validate(run)


@router.post("/job-relevance", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
def run_job_relevance_evaluation(db: Session = Depends(get_db)) -> EvaluationRunResponse:
    run = EvaluationService().run_job_relevance_evaluation(db)
    return EvaluationRunResponse.model_validate(run)


@router.post("/application-packet", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
def run_application_packet_evaluation(db: Session = Depends(get_db)) -> EvaluationRunResponse:
    run = EvaluationService().run_application_packet_evaluation(db)
    return EvaluationRunResponse.model_validate(run)


@router.post("/real-job-source-smoke", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
async def run_real_job_source_smoke(
    query: str = Query(default="Agent 开发实习生", min_length=1),
    location: str | None = Query(default=None),
    limit: int = Query(default=8, ge=1, le=20),
    sources: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> EvaluationRunResponse:
    run = await EvaluationService().run_real_job_source_smoke(
        db,
        query=query,
        location=location,
        limit=limit,
        sources=sources,
    )
    return EvaluationRunResponse.model_validate(run)


@router.post("/real-job-ingest-smoke", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
async def run_real_job_ingest_smoke(
    query: str = Query(default="Agent 开发实习生", min_length=1),
    location: str | None = Query(default=None),
    limit: int = Query(default=3, ge=1, le=8),
    sources: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> EvaluationRunResponse:
    run = await EvaluationService().run_real_job_ingest_smoke(
        db,
        query=query,
        location=location,
        limit=limit,
        sources=sources,
    )
    return EvaluationRunResponse.model_validate(run)


@router.post("/llm-workflow", response_model=EvaluationRunResponse, status_code=status.HTTP_201_CREATED)
async def run_llm_workflow_evaluation(
    case_limit: int | None = Query(default=None, ge=1, le=18),
    resume_from_last_completed: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> EvaluationRunResponse:
    run = await EvaluationService().run_llm_workflow_evaluation(
        db,
        case_limit=case_limit,
        resume_from_last_completed=resume_from_last_completed,
    )
    return EvaluationRunResponse.model_validate(run)


@router.get("/results", response_model=list[EvaluationRunResponse])
def list_evaluation_runs(db: Session = Depends(get_db)) -> list[EvaluationRunResponse]:
    rows = db.query(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(50).all()
    return [EvaluationRunResponse.model_validate(row) for row in rows]
