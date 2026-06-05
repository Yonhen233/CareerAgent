from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import Profile
from app.models.schemas import GuidedProfileRequest, ProfileResponse
from app.services.resume_parser import ResumeParserService

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("/upload", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
async def upload_resume(file: UploadFile = File(...), db: Session = Depends(get_db)) -> ProfileResponse:
    try:
        content = await file.read()
        profile = await ResumeParserService().create_profile_from_pdf(
            db,
            filename=file.filename or "resume.pdf",
            file_bytes=content,
        )
        return ProfileResponse.model_validate(profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse resume: {exc}") from exc


@router.post("/guided", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
def create_guided_profile(payload: GuidedProfileRequest, db: Session = Depends(get_db)) -> ProfileResponse:
    profile = ResumeParserService().create_profile_from_guided_answers(db, payload)
    return ProfileResponse.model_validate(profile)


@router.get("", response_model=list[ProfileResponse])
def list_profiles(db: Session = Depends(get_db)) -> list[ProfileResponse]:
    rows = db.query(Profile).order_by(Profile.created_at.desc()).all()
    return [ProfileResponse.model_validate(row) for row in rows]


@router.get("/{profile_id}", response_model=ProfileResponse)
def get_profile(profile_id: int, db: Session = Depends(get_db)) -> ProfileResponse:
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return ProfileResponse.model_validate(profile)
