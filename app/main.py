from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.agent_runs import router as agent_runs_router
from app.api.agent_skills import router as agent_skills_router
from app.api.agent_tools import router as agent_tools_router
from app.api.applications import router as applications_router
from app.api.evaluations import router as evaluations_router
from app.api.health import router as health_router
from app.api.interview_prep import router as interview_prep_router
from app.api.jobs import router as jobs_router
from app.api.llm_debug import router as llm_debug_router
from app.api.matches import router as matches_router
from app.api.profiles import router as profiles_router
from app.api.resumes import router as resumes_router
from app.core.config import get_settings
from app.core.database import init_db
from app.frontend.routes import router as frontend_router

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    settings.export_path.mkdir(parents=True, exist_ok=True)
    settings.chroma_path.mkdir(parents=True, exist_ok=True)
    init_db()
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

app.include_router(frontend_router)
app.include_router(health_router)
app.include_router(profiles_router)
app.include_router(jobs_router)
app.include_router(llm_debug_router)
app.include_router(matches_router)
app.include_router(resumes_router)
app.include_router(applications_router)
app.include_router(interview_prep_router)
app.include_router(evaluations_router)
app.include_router(agent_tools_router)
app.include_router(agent_skills_router)
app.include_router(agent_runs_router)
