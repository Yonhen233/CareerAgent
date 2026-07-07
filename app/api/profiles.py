from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import get_settings
from app.core.security import AuthContext, optional_auth_context
from app.models.entities import Profile
from app.models.schemas import GuidedProfileRequest, ProfileResponse
from app.services.resume_delivery import ResumeHTMLRenderer
from app.services.resume_parser import ResumeParserService

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("/upload", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
def _apply_tenant(query, auth: AuthContext):
    settings = get_settings()
    if settings.rbac_enabled:
        return query.filter(Profile.tenant_id == auth.tenant_id)
    return query


def _set_tenant(profile: Profile, auth: AuthContext, db: Session) -> Profile:
    if get_settings().rbac_enabled:
        profile.tenant_id = auth.tenant_id
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


async def upload_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> ProfileResponse:
    try:
        content = await file.read()
        profile = await ResumeParserService().create_profile_from_pdf(
            db,
            filename=file.filename or "resume.pdf",
            file_bytes=content,
        )
        return ProfileResponse.model_validate(_set_tenant(profile, auth, db))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse resume: {exc}") from exc


@router.post("/guided", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
def create_guided_profile(
    payload: GuidedProfileRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> ProfileResponse:
    profile = ResumeParserService().create_profile_from_guided_answers(db, payload)
    return ProfileResponse.model_validate(_set_tenant(profile, auth, db))


@router.get("", response_model=list[ProfileResponse])
def list_profiles(db: Session = Depends(get_db), auth: AuthContext = Depends(optional_auth_context)) -> list[ProfileResponse]:
    rows = _apply_tenant(db.query(Profile), auth).order_by(Profile.created_at.desc()).all()
    return [ProfileResponse.model_validate(row) for row in rows]


@router.get("/{profile_id}", response_model=ProfileResponse)
def get_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> ProfileResponse:
    profile = _apply_tenant(db.query(Profile), auth).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return ProfileResponse.model_validate(profile)


@router.get("/{profile_id}/html")
def preview_profile_html(
    profile_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> Response:
    profile = _apply_tenant(db.query(Profile), auth).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    html = ResumeHTMLRenderer().render_profile(profile)
    return Response(content=html, media_type="text/html; charset=utf-8")
