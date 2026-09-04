import hashlib
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import get_settings
from app.core.security import AuthContext, optional_auth_context
from app.core.llm import format_exception
from app.models.entities import Job, JobChunk
from app.models.schemas import JobChunkResponse, JobCreateRequest, JobResponse, JobSearchRequest, JobSearchResponse
from app.services.jd_parser import JDParserService
from app.services.job_search import JobSearchService
from app.services.job_visibility import user_visible_jobs
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreateRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> JobResponse:
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
    digest = hashlib.sha1(payload.jd_text.encode("utf-8")).hexdigest()
    external_id = f"{auth.tenant_id}:manual:{digest}" if get_settings().rbac_enabled else f"manual:{digest}"
    existing_query = db.query(Job).filter(Job.source == "manual", Job.external_id == external_id)
    if get_settings().rbac_enabled:
        existing_query = existing_query.filter(Job.tenant_id == auth.tenant_id)
    existing = existing_query.first()
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
        tenant_id=auth.tenant_id if get_settings().rbac_enabled else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _index_job_chunks(db, job)
    return JobResponse.model_validate(job)


@router.post("/search", response_model=JobSearchResponse)
async def search_jobs(
    payload: JobSearchRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> JobSearchResponse:
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
        if get_settings().rbac_enabled:
            for job in persisted:
                job.tenant_id = auth.tenant_id
                db.add(job)
            db.commit()
        return JobSearchResponse(
            jobs=[JobResponse.model_validate(job) for job in persisted],
            source_errors=source_errors,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Job search failed: {exc}") from exc


@router.get("", response_model=list[JobResponse])
def list_jobs(db: Session = Depends(get_db), auth: AuthContext = Depends(optional_auth_context)) -> list[JobResponse]:
    query = db.query(Job)
    if get_settings().rbac_enabled:
        query = query.filter(Job.tenant_id == auth.tenant_id)
    rows = user_visible_jobs(query.order_by(Job.discovered_at.desc()).limit(400).all())[:200]
    return [JobResponse.model_validate(row) for row in rows]


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(optional_auth_context)) -> JobResponse:
    query = db.query(Job).filter(Job.id == job_id)
    if get_settings().rbac_enabled:
        query = query.filter(Job.tenant_id == auth.tenant_id)
    job = query.first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobResponse.model_validate(job)


@router.get("/{job_id}/html")
def preview_job_html(job_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(optional_auth_context)) -> Response:
    query = db.query(Job).filter(Job.id == job_id)
    if get_settings().rbac_enabled:
        query = query.filter(Job.tenant_id == auth.tenant_id)
    job = query.first()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    structured = job.structured_jd_json or {}
    sections = [
        ("职责", structured.get("responsibilities") or []),
        ("要求", structured.get("qualifications") or []),
        ("必备技能", structured.get("required_skills") or []),
        ("加分技能", structured.get("preferred_skills") or []),
    ]
    section_html = "\n".join(
        f"<section><h2>{escape(title)}</h2><ul>{''.join(f'<li>{escape(str(item))}</li>' for item in items)}</ul></section>"
        for title, items in sections
        if items
    )
    apply_link = f'<a href="{escape(job.apply_url)}" target="_blank">打开投递链接</a>' if job.apply_url else ""
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(job.title)} · JD 预览</title>
  <style>
    body {{ margin: 0; background: #f4f7fb; color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.65; }}
    main {{ max-width: 860px; margin: 32px auto; padding: 32px; border: 1px solid #d7e0ec; border-radius: 12px; background: #fff; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    h2 {{ margin: 24px 0 8px; font-size: 18px; }}
    .meta {{ color: #64748b; }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0; }}
    .tag {{ padding: 5px 9px; border-radius: 999px; background: #edf2f7; color: #37506b; font-size: 13px; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; padding: 18px; border-radius: 8px; background: #f8fafc; }}
    a {{ color: #1d64d8; font-weight: 700; }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(job.title)}</h1>
    <p class="meta">{escape(job.company or "未知公司")} · {escape(job.location or "地点未填写")} · {escape(job.source)}</p>
    {f'<p>{apply_link}</p>' if apply_link else ''}
    <div class="tags">{''.join(f'<span class="tag">{escape(str(item))}</span>' for item in (structured.get("keywords") or structured.get("required_skills") or [])[:12])}</div>
    {section_html}
    <section>
      <h2>原始 JD</h2>
      <pre>{escape(job.raw_jd_text or "")}</pre>
    </section>
  </main>
</body>
</html>"""
    return Response(content=html, media_type="text/html; charset=utf-8")


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
