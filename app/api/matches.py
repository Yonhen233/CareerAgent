from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import Job, MatchResult, Profile
from app.models.schemas import MatchCreateRequest, MatchResponse
from app.services.matcher import MatcherService

router = APIRouter(prefix="/matches", tags=["matches"])


@router.post("", response_model=MatchResponse, status_code=status.HTTP_201_CREATED)
def create_match(payload: MatchCreateRequest, db: Session = Depends(get_db)) -> MatchResponse:
    profile = db.query(Profile).filter(Profile.id == payload.profile_id).first()
    job = db.query(Job).filter(Job.id == payload.job_id).first()
    if profile is None or job is None:
        raise HTTPException(status_code=404, detail="Profile or job not found.")
    result = MatcherService().create_match_result(db, profile, job)
    return MatchResponse.model_validate(result)


@router.get("", response_model=list[MatchResponse])
def list_matches(db: Session = Depends(get_db)) -> list[MatchResponse]:
    rows = db.query(MatchResult).order_by(MatchResult.created_at.desc()).limit(200).all()
    return [MatchResponse.model_validate(row) for row in rows]
