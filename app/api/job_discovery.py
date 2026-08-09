from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import AuthContext, optional_auth_context
from app.models.entities import JobSearchSession
from app.models.schemas import (
    JobDiscoveryRequest,
    JobDiscoveryResultResponse,
    JobDiscoverySessionResponse,
    JobDiscoverySessionSummary,
    JobResponse,
)
from app.services.job_discovery import JobDiscoveryService
from app.services.retrieval_quality import RetrievalQualityError

router = APIRouter(prefix="/job-discovery", tags=["job-discovery"])


@router.post(
    "/sessions",
    response_model=JobDiscoverySessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_discovery_session(
    payload: JobDiscoveryRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> JobDiscoverySessionResponse:
    try:
        session = await JobDiscoveryService().discover(db, payload, tenant_id=auth.tenant_id)
    except RetrievalQualityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(exc), "retrieval_quality": exc.report},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"岗位检索失败：{exc}") from exc
    return _response(session)


@router.get("/sessions/{session_id}", response_model=JobDiscoverySessionResponse)
def get_discovery_session(
    session_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> JobDiscoverySessionResponse:
    session = JobDiscoveryService().get_session(db, session_id, tenant_id=auth.tenant_id)
    if session is None:
        raise HTTPException(status_code=404, detail="岗位搜索记录不存在。")
    return _response(session)


@router.get("/sessions", response_model=list[JobDiscoverySessionSummary])
def list_discovery_sessions(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> list[JobDiscoverySessionSummary]:
    query = db.query(JobSearchSession)
    if get_settings().rbac_enabled:
        query = query.filter(JobSearchSession.tenant_id == auth.tenant_id)
    rows = query.order_by(JobSearchSession.created_at.desc()).limit(50).all()
    return [JobDiscoverySessionSummary.model_validate(row) for row in rows]


def _response(session: JobSearchSession) -> JobDiscoverySessionResponse:
    return JobDiscoverySessionResponse(
        session=JobDiscoverySessionSummary.model_validate(session),
        results=[
            JobDiscoveryResultResponse(
                id=result.id,
                rank=result.rank,
                retrieval_score=result.retrieval_score,
                match_score=result.match_score,
                final_score=result.final_score,
                match_result_id=result.match_result_id,
                reason=result.reason_json or {},
                job=JobResponse.model_validate(result.job),
            )
            for result in session.results
        ],
    )
