from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings

settings = get_settings()
templates = Jinja2Templates(directory=str(settings.base_path / "app" / "templates"))
router = APIRouter(tags=["frontend"])


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"page": "dashboard"})


@router.get("/ui/profiles", response_class=HTMLResponse)
def profiles(request: Request):
    return templates.TemplateResponse(request, "profiles.html", {"page": "profiles"})


@router.get("/ui/jobs", response_class=HTMLResponse)
def jobs(request: Request):
    return templates.TemplateResponse(request, "jobs.html", {"page": "jobs"})


@router.get("/ui/agent-runs", response_class=HTMLResponse)
def agent_runs(request: Request):
    return templates.TemplateResponse(request, "agent_runs.html", {"page": "agent_runs"})


@router.get("/ui/resumes", response_class=HTMLResponse)
def resumes(request: Request):
    return templates.TemplateResponse(request, "resumes.html", {"page": "resumes"})


@router.get("/ui/applications", response_class=HTMLResponse)
def applications(request: Request):
    return templates.TemplateResponse(request, "applications.html", {"page": "applications"})


@router.get("/ui/interview-prep", response_class=HTMLResponse)
def interview_prep(request: Request):
    return templates.TemplateResponse(request, "interview_prep.html", {"page": "interview_prep"})
