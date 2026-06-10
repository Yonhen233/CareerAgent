import hashlib

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.llm import format_exception
from app.models.entities import Job, JobChunk
from app.models.schemas import JobChunkResponse, JobCreateRequest, JobResponse, JobSearchRequest, JobSearchResponse
from app.services.jd_parser import JDParserService
from app.services.job_search import JobSearchService
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreateRequest, db: Session = Depends(get_db)) -> JobResponse:
    try:
        structured = await JDParserService().parse_jd(
            payload.jd_text,
            title=payload.title,
            company=payload.company,
            location=payload.location,
            db=db,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"JD parsing LLM generation failed: {format_exception(exc)}") from exc
    external_id = f"manual:{hashlib.sha1(payload.jd_text.encode('utf-8')).hexdigest()}"
    existing = db.query(Job).filter(Job.source == "manual", Job.external_id == external_id).first()
    if existing:
        existing.title = payload.title or structured.get("title") or existing.title
        existing.company = payload.company or structured.get("company")
        existing.location = payload.location or structured.get("location")
        existing.job_type = structured.get("job_type")
        existing.apply_url = str(payload.apply_url) if payload.apply_url else existing.apply_url
        existing.raw_jd_text = payload.jd_text
        existing.structured_jd_json = structured
        db.commit()
        db.refresh(existing)
        _index_job_chunks(db, existing)
        return JobResponse.model_validate(existing)

    job = Job(
        source="manual",
        external_id=external_id,
        title=payload.title or structured.get("title") or "Untitled Job",
        company=payload.company or structured.get("company"),
        location=payload.location or structured.get("location"),
        job_type=structured.get("job_type"),
        apply_url=str(payload.apply_url) if payload.apply_url else None,
        raw_jd_text=payload.jd_text,
        structured_jd_json=structured,
        source_payload_json={"created_by": "manual"},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _index_job_chunks(db, job)
    return JobResponse.model_validate(job)


@router.post("/search", response_model=JobSearchResponse)
async def search_jobs(payload: JobSearchRequest, db: Session = Depends(get_db)) -> JobSearchResponse:
    try:
        jobs, source_errors = await JobSearchService().search(
            db,
            query=payload.query,
            location=payload.location,
            internship_only=payload.internship_only,
            limit=payload.limit,
            sources=payload.sources,
            store_results=payload.store_results,
        )
        persisted = [job for job in jobs if getattr(job, "id", None)]
        return JobSearchResponse(
            jobs=[JobResponse.model_validate(job) for job in persisted],
            source_errors=source_errors,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Job search failed: {exc}") from exc


@router.get("", response_model=list[JobResponse])
def list_jobs(db: Session = Depends(get_db)) -> list[JobResponse]:
    rows = db.query(Job).order_by(Job.discovered_at.desc()).limit(200).all()
    return [JobResponse.model_validate(row) for row in rows]


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobResponse:
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobResponse.model_validate(job)


@router.get("/{job_id}/chunks", response_model=list[JobChunkResponse])
def get_job_chunks(job_id: int, db: Session = Depends(get_db)) -> list[JobChunkResponse]:
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    chunks = db.query(JobChunk).filter(JobChunk.job_id == job_id).order_by(JobChunk.id.asc()).all()
    return [JobChunkResponse.model_validate(chunk) for chunk in chunks]


def _index_job_chunks(db: Session, job: Job) -> int:
    chunks = ResumeTextSplitter().split_jd_text(job.raw_jd_text, job.structured_jd_json or {}, prefix=f"job_{job.id}")
    return SQLiteVectorIndex().upsert_job_chunks(db, job.id, chunks)
