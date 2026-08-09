from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.agents.natural_language import NaturalLanguageAgentService
from app.core.database import get_db
from app.core.llm import format_exception
from app.core.security import AuthContext, optional_auth_context
from app.core.config import get_settings
from app.models.entities import Job, Profile
from app.models.schemas import NaturalLanguageAgentRequest, NaturalLanguageAgentResponse

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.post(
    "/natural-language",
    response_model=NaturalLanguageAgentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def run_natural_language_request(
    payload: NaturalLanguageAgentRequest,
    response: Response,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> NaturalLanguageAgentResponse:
    if get_settings().rbac_enabled and payload.profile_id is not None:
        profile = db.query(Profile).filter(Profile.id == payload.profile_id).first()
        if profile is None or profile.tenant_id != auth.tenant_id:
            raise HTTPException(status_code=404, detail="Profile not found.")
    if get_settings().rbac_enabled and payload.job_id is not None:
        job = db.query(Job).filter(Job.id == payload.job_id).first()
        if job is None or job.tenant_id not in {None, auth.tenant_id}:
            raise HTTPException(status_code=404, detail="Job not found.")
    service = NaturalLanguageAgentService()
    try:
        run = await service.run(
            db,
            payload,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Natural language agent failed: {format_exception(exc)}",
        ) from exc
    output = run.output_json or {}
    if run.status == "failed":
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return NaturalLanguageAgentResponse.model_validate(output)
