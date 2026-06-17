from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import Job, Profile, ResumeVersion
from app.models.schemas import ResumeVersionResponse, TailorResumeRequest
from app.services.resume_delivery import ResumeHTMLRenderer
from app.services.resume_tailor import ResumeTailorService

router = APIRouter(prefix="/resumes", tags=["resumes"])


@router.post("/tailor", response_model=ResumeVersionResponse, status_code=status.HTTP_201_CREATED)
async def tailor_resume(payload: TailorResumeRequest, db: Session = Depends(get_db)) -> ResumeVersionResponse:
    profile = db.query(Profile).filter(Profile.id == payload.profile_id).first()
    job = db.query(Job).filter(Job.id == payload.job_id).first()
    if profile is None or job is None:
        raise HTTPException(status_code=404, detail="Profile or job not found.")
    version = await ResumeTailorService().tailor_resume(db, profile, job)
    return ResumeVersionResponse.model_validate(version)


@router.get("", response_model=list[ResumeVersionResponse])
def list_resume_versions(db: Session = Depends(get_db)) -> list[ResumeVersionResponse]:
    rows = db.query(ResumeVersion).order_by(ResumeVersion.created_at.desc()).limit(200).all()
    return [ResumeVersionResponse.model_validate(row) for row in rows]


@router.get("/{resume_version_id}", response_model=ResumeVersionResponse)
def get_resume_version(resume_version_id: int, db: Session = Depends(get_db)) -> ResumeVersionResponse:
    version = db.query(ResumeVersion).filter(ResumeVersion.id == resume_version_id).first()
    if version is None:
        raise HTTPException(status_code=404, detail="Resume version not found.")
    return ResumeVersionResponse.model_validate(version)


@router.get("/{resume_version_id}/markdown")
def download_markdown(resume_version_id: int, db: Session = Depends(get_db)) -> Response:
    version = db.query(ResumeVersion).filter(ResumeVersion.id == resume_version_id).first()
    if version is None:
        raise HTTPException(status_code=404, detail="Resume version not found.")
    filename = f"resume_version_{resume_version_id}.md"
    return Response(
        content=version.tailored_resume_markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{resume_version_id}/html")
def preview_resume_html(resume_version_id: int, db: Session = Depends(get_db)) -> Response:
    version = db.query(ResumeVersion).filter(ResumeVersion.id == resume_version_id).first()
    if version is None:
        raise HTTPException(status_code=404, detail="Resume version not found.")
    html = ResumeHTMLRenderer().render_resume_version(version)
    return Response(content=html, media_type="text/html; charset=utf-8")
