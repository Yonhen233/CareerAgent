from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import InterviewExperience, InterviewPrep, Job, Profile
from app.models.schemas import (
    InterviewExperienceCreateRequest,
    InterviewExperienceResponse,
    InterviewPracticeItemResponse,
    InterviewPracticeItemUpdateRequest,
    InterviewPrepRequest,
    InterviewPrepResponse,
)
from app.services.interview_delivery import InterviewPrepDeliveryService
from app.services.interview_experience import InterviewExperienceService
from app.services.interview_prep import InterviewPrepService

router = APIRouter(prefix="/interview-prep", tags=["interview-prep"])


@router.post("", response_model=InterviewPrepResponse, status_code=status.HTTP_201_CREATED)
async def create_interview_prep(
    payload: InterviewPrepRequest,
    db: Session = Depends(get_db),
) -> InterviewPrepResponse:
    profile = db.query(Profile).filter(Profile.id == payload.profile_id).first()
    job = db.query(Job).filter(Job.id == payload.job_id).first()
    if profile is None or job is None:
        raise HTTPException(status_code=404, detail="Profile or job not found.")
    prep = await InterviewPrepService().create_interview_prep_with_llm(
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


@router.get("/{prep_id}/questions")
def list_interview_prep_questions(prep_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    prep = _get_interview_prep_or_404(db, prep_id)
    delivery = InterviewPrepDeliveryService()
    rows = delivery.list_practice_items(db, prep)
    return {
        "interview_prep_id": prep.id,
        "questions": delivery.question_items(prep),
        "source_perspective_summary": delivery.source_perspective_summary(prep),
        "practice_summary": delivery.progress_summary(prep, rows),
    }


@router.get("/{prep_id}/practice")
def get_interview_practice(prep_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    prep = _get_interview_prep_or_404(db, prep_id)
    delivery = InterviewPrepDeliveryService()
    rows = delivery.list_practice_items(db, prep)
    return {
        "interview_prep_id": prep.id,
        "practice_items": [InterviewPracticeItemResponse.model_validate(row).model_dump(mode="json") for row in rows],
        "source_perspective_summary": delivery.source_perspective_summary(prep),
        "practice_summary": delivery.progress_summary(prep, rows),
    }


@router.put("/{prep_id}/practice", response_model=InterviewPracticeItemResponse)
def update_interview_practice(
    prep_id: int,
    payload: InterviewPracticeItemUpdateRequest,
    db: Session = Depends(get_db),
) -> InterviewPracticeItemResponse:
    prep = _get_interview_prep_or_404(db, prep_id)
    try:
        row = InterviewPrepDeliveryService().upsert_practice_item(
            db,
            prep,
            question_id=payload.question_id,
            status=payload.status,
            confidence_score=payload.confidence_score,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return InterviewPracticeItemResponse.model_validate(row)


@router.get("/{prep_id}/markdown")
def export_interview_prep_markdown(prep_id: int, db: Session = Depends(get_db)) -> Response:
    prep = _get_interview_prep_or_404(db, prep_id)
    delivery = InterviewPrepDeliveryService()
    markdown = delivery.render_markdown(prep, practice_items=delivery.list_practice_items(db, prep))
    filename = f"interview-prep-{prep.id}.md"
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{prep_id}", response_model=InterviewPrepResponse)
def get_interview_prep(prep_id: int, db: Session = Depends(get_db)) -> InterviewPrepResponse:
    return InterviewPrepResponse.model_validate(_get_interview_prep_or_404(db, prep_id))


def _get_interview_prep_or_404(db: Session, prep_id: int) -> InterviewPrep:
    prep = db.query(InterviewPrep).filter(InterviewPrep.id == prep_id).first()
    if prep is None:
        raise HTTPException(status_code=404, detail="Interview prep not found.")
    return prep
