from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import Application, Job, Profile, ResumeVersion
from app.models.schemas import ApplicationResponse, QuickApplyRequest
from app.services.application_service import ApplicationService

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("/quick-apply", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
async def quick_apply(payload: QuickApplyRequest, db: Session = Depends(get_db)) -> ApplicationResponse:
    profile = db.query(Profile).filter(Profile.id == payload.profile_id).first()
    job = db.query(Job).filter(Job.id == payload.job_id).first()
    if profile is None or job is None:
        raise HTTPException(status_code=404, detail="Profile or job not found.")
    version = None
    if payload.resume_version_id:
        version = db.query(ResumeVersion).filter(ResumeVersion.id == payload.resume_version_id).first()
    application = await ApplicationService().create_quick_apply_packet(
        db,
        profile=profile,
        job=job,
        resume_version=version,
        browser_assist=payload.browser_assist,
    )
    return ApplicationResponse.model_validate(application)


@router.get("", response_model=list[ApplicationResponse])
def list_applications(db: Session = Depends(get_db)) -> list[ApplicationResponse]:
    rows = db.query(Application).order_by(Application.created_at.desc()).limit(200).all()
    return [ApplicationResponse.model_validate(row) for row in rows]
