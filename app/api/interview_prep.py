from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import InterviewPrep, Job, Profile
from app.models.schemas import InterviewPrepRequest, InterviewPrepResponse
from app.services.interview_prep import InterviewPrepService

router = APIRouter(prefix="/interview-prep", tags=["interview-prep"])


@router.post("", response_model=InterviewPrepResponse, status_code=status.HTTP_201_CREATED)
def create_interview_prep(
    payload: InterviewPrepRequest,
    db: Session = Depends(get_db),
) -> InterviewPrepResponse:
    profile = db.query(Profile).filter(Profile.id == payload.profile_id).first()
    job = db.query(Job).filter(Job.id == payload.job_id).first()
    if profile is None or job is None:
        raise HTTPException(status_code=404, detail="Profile or job not found.")
    prep = InterviewPrepService().create_interview_prep(db, profile=profile, job=job)
    return InterviewPrepResponse.model_validate(prep)


@router.get("", response_model=list[InterviewPrepResponse])
def list_interview_preps(db: Session = Depends(get_db)) -> list[InterviewPrepResponse]:
    rows = db.query(InterviewPrep).order_by(InterviewPrep.created_at.desc()).limit(200).all()
    return [InterviewPrepResponse.model_validate(row) for row in rows]


@router.get("/{prep_id}", response_model=InterviewPrepResponse)
def get_interview_prep(prep_id: int, db: Session = Depends(get_db)) -> InterviewPrepResponse:
    prep = db.query(InterviewPrep).filter(InterviewPrep.id == prep_id).first()
    if prep is None:
        raise HTTPException(status_code=404, detail="Interview prep not found.")
    return InterviewPrepResponse.model_validate(prep)
