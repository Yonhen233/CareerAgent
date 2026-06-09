from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import InterviewExperience, InterviewPrep, Job, Profile
from app.models.schemas import (
    InterviewExperienceCreateRequest,
    InterviewExperienceResponse,
    InterviewPrepRequest,
    InterviewPrepResponse,
)
from app.services.interview_experience import InterviewExperienceService
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
    prep = InterviewPrepService().create_interview_prep(
        db,
        profile=profile,
        job=job,
        experience_ids=payload.experience_ids,
    )
    return InterviewPrepResponse.model_validate(prep)


@router.get("", response_model=list[InterviewPrepResponse])
def list_interview_preps(db: Session = Depends(get_db)) -> list[InterviewPrepResponse]:
    rows = db.query(InterviewPrep).order_by(InterviewPrep.created_at.desc()).limit(200).all()
    return [InterviewPrepResponse.model_validate(row) for row in rows]


@router.post("/experiences", response_model=InterviewExperienceResponse, status_code=status.HTTP_201_CREATED)
def create_interview_experience(
    payload: InterviewExperienceCreateRequest,
    db: Session = Depends(get_db),
) -> InterviewExperienceResponse:
    job = None
    if payload.job_id is not None:
        job = db.query(Job).filter(Job.id == payload.job_id).first()
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
    try:
        row = InterviewExperienceService().create_experience(
            db,
            source_site=payload.source_site,
            source_url=payload.source_url,
            title=payload.title,
            company=payload.company,
            role_keyword=payload.role_keyword,
            raw_text=payload.raw_text,
            job=job,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return InterviewExperienceResponse.model_validate(row)


@router.get("/experiences", response_model=list[InterviewExperienceResponse])
def list_interview_experiences(
    job_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[InterviewExperienceResponse]:
    query = db.query(InterviewExperience)
    if job_id is not None:
        query = query.filter(InterviewExperience.job_id == job_id)
    rows = query.order_by(InterviewExperience.created_at.desc()).limit(200).all()
    return [InterviewExperienceResponse.model_validate(row) for row in rows]


@router.get("/experiences/{experience_id}", response_model=InterviewExperienceResponse)
def get_interview_experience(
    experience_id: int,
    db: Session = Depends(get_db),
) -> InterviewExperienceResponse:
    row = db.query(InterviewExperience).filter(InterviewExperience.id == experience_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Interview experience not found.")
    return InterviewExperienceResponse.model_validate(row)


@router.get("/{prep_id}", response_model=InterviewPrepResponse)
def get_interview_prep(prep_id: int, db: Session = Depends(get_db)) -> InterviewPrepResponse:
    prep = db.query(InterviewPrep).filter(InterviewPrep.id == prep_id).first()
    if prep is None:
        raise HTTPException(status_code=404, detail="Interview prep not found.")
    return InterviewPrepResponse.model_validate(prep)
