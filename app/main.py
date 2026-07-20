from contextlib import asynccontextmanager
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.agent_runs import router as agent_runs_router
from app.api.agent_skills import router as agent_skills_router
from app.api.agent_tools import router as agent_tools_router
from app.api.applications import router as applications_router
from app.api.assistant import router as assistant_router
from app.api.auth import router as auth_router
from app.api.evaluations import router as evaluations_router
from app.api.health import router as health_router
from app.api.interview_prep import router as interview_prep_router
from app.api.job_discovery import router as job_discovery_router
from app.api.jobs import router as jobs_router
from app.api.llm_debug import router as llm_debug_router
from app.api.matches import router as matches_router
from app.api.ops import router as ops_router
from app.api.profiles import router as profiles_router
from app.api.resumes import router as resumes_router
from app.api.tasks import router as tasks_router
from app.core.config import get_settings
from app.core.database import init_db
from app.core.security import request_has_mutation_access
from app.core.telemetry import telemetry
from app.services.session_auth import SessionAuthService
from app.frontend.routes import router as frontend_router

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    settings.export_path.mkdir(parents=True, exist_ok=True)
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    settings.outbound_email_draft_path.mkdir(parents=True, exist_ok=True)
    settings.supervisor_health_path.parent.mkdir(parents=True, exist_ok=True)
    init_db()
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        SessionAuthService(settings=settings).ensure_bootstrap_admin(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "A production-shaped job-search agent for resume parsing, PDF chunking, "
        "SQLite-backed RAG, real career-site job search, resume tailoring, and application tracking."
    ),
    lifespan=lifespan,
)


app.mount("/static", StaticFiles(directory=str(settings.base_path / "app" / "static")), name="static")


@app.middleware("http")
async def record_request_metrics(request, call_next):
    started = time.perf_counter()
    if (
        settings.require_admin_for_mutations
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and not request_has_mutation_access(request.headers, request.cookies)
    ):
        response = JSONResponse(
            status_code=401,
            content={"detail": "Admin token is required for write operations."},
        )
        telemetry.record(
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        return response
    response = await call_next(request)
    telemetry.record(
        path=request.url.path,
        status_code=response.status_code,
        latency_ms=(time.perf_counter() - started) * 1000,
    )
    return response

app.include_router(frontend_router)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(ops_router)
app.include_router(profiles_router)
app.include_router(jobs_router)
app.include_router(job_discovery_router)
app.include_router(llm_debug_router)
app.include_router(matches_router)
app.include_router(resumes_router)
app.include_router(applications_router)
app.include_router(assistant_router)
app.include_router(interview_prep_router)
app.include_router(evaluations_router)
app.include_router(agent_tools_router)
app.include_router(agent_skills_router)
app.include_router(agent_runs_router)
app.include_router(tasks_router)
