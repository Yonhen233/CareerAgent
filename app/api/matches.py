from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.llm import format_exception
from app.core.database import get_db
from app.models.entities import Job, MatchResult, Profile
from app.models.schemas import MatchCreateRequest, MatchResponse
from app.services.matcher import MatcherService
from app.services.semantic_match_analysis import SemanticMatchAnalysisService

router = APIRouter(prefix="/matches", tags=["matches"])


@router.post("", response_model=MatchResponse, status_code=status.HTTP_201_CREATED)
async def create_match(payload: MatchCreateRequest, db: Session = Depends(get_db)) -> MatchResponse:
    profile = db.query(Profile).filter(Profile.id == payload.profile_id).first()
    job = db.query(Job).filter(Job.id == payload.job_id).first()
    if profile is None or job is None:
        raise HTTPException(status_code=404, detail="Profile or job not found.")
    try:
        matcher = MatcherService()
        baseline = matcher.build_match_payload(db, profile, job)
        analysis = await SemanticMatchAnalysisService().analyze(
            db,
            profile=profile,
            job=job,
            baseline=baseline,
        )
        result = matcher.create_match_result(db, profile, job, payload=analysis.payload)
        retrieval_quality = dict(result.retrieval_quality_json or {})
        retrieval_quality["semantic_match"] = analysis.metadata
        retrieval_quality["semantic_match"]["warning"] = analysis.warning
        result.retrieval_quality_json = retrieval_quality
        db.add(result)
        db.commit()
        db.refresh(result)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Match generation failed: {format_exception(exc)}") from exc
    return MatchResponse.model_validate(result)


@router.get("", response_model=list[MatchResponse])
def list_matches(db: Session = Depends(get_db)) -> list[MatchResponse]:
    rows = db.query(MatchResult).order_by(MatchResult.created_at.desc()).limit(200).all()
    return [MatchResponse.model_validate(row) for row in rows]
