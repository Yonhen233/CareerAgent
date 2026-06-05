from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class EducationItem(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    duration: str = ""
    details: str = ""


class ProjectItem(BaseModel):
    name: str = ""
    description: str = ""
    tech_stack: list[str] = Field(default_factory=list)
    impact: str = ""


class ExperienceItem(BaseModel):
    company: str = ""
    role: str = ""
    duration: str = ""
    details: str = ""
    tech_stack: list[str] = Field(default_factory=list)


class ProfileStructured(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    headline: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    work_experience: list[ExperienceItem] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    raw_text: str = ""


class GuidedProfileRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    headline: str | None = "Agent 开发实习生候选人"
    target_roles: list[str] = Field(default_factory=lambda: ["Agent 开发实习生"])
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    work_experience: list[ExperienceItem] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str | None
    email: str | None
    phone: str | None
    headline: str | None
    target_roles_json: list[str]
    source_type: str
    structured_profile_json: dict[str, Any]
    created_at: datetime


class JDStructured(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    job_type: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    seniority: str | None = None


class JobCreateRequest(BaseModel):
    jd_text: str = Field(min_length=20)
    title: str | None = None
    company: str | None = None
    location: str | None = None
    apply_url: HttpUrl | None = None


class JobSearchRequest(BaseModel):
    query: str = "Agent intern"
    location: str | None = None
    internship_only: bool = True
    limit: int = Field(default=20, ge=1, le=80)
    sources: list[str] = Field(default_factory=lambda: ["tencent", "lever"])
    store_results: bool = True


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    external_id: str
    title: str
    company: str | None
    location: str | None
    job_type: str | None
    apply_url: str | None
    raw_jd_text: str
    structured_jd_json: dict[str, Any]
    discovered_at: datetime


class JobSearchResponse(BaseModel):
    jobs: list[JobResponse]
    source_errors: dict[str, str] = Field(default_factory=dict)


class MatchCreateRequest(BaseModel):
    profile_id: int
    job_id: int


class MatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    job_id: int
    overall_score: float
    dimension_scores_json: dict[str, float]
    matched_skills_json: list[str]
    missing_skills_json: list[str]
    relevant_evidence_json: list[dict[str, Any]]
    suggestions_json: list[str]
    created_at: datetime


class TailorResumeRequest(BaseModel):
    profile_id: int
    job_id: int


class ResumeVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    job_id: int
    title: str
    tailored_resume_markdown: str
    change_summary_json: list[dict[str, Any]]
    keyword_alignment_json: dict[str, Any]
    source_evidence_json: list[dict[str, Any]]
    verification_json: dict[str, Any]
    diff_text: str | None
    created_at: datetime


class AgentRunRequest(BaseModel):
    task_type: Literal["find_jobs_for_profile", "tailor_resume_for_job", "quick_apply"]
    profile_id: int | None = None
    job_id: int | None = None
    resume_version_id: int | None = None
    query: str | None = "Agent intern"
    location: str | None = None
    limit: int = Field(default=20, ge=1, le=80)


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_type: str
    profile_id: int | None
    job_id: int | None
    status: str
    input_json: dict[str, Any]
    output_json: dict[str, Any] | None
    error_message: str | None
    latency_ms: int
    created_at: datetime


class AgentStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    step_name: str
    tool_name: str | None
    status: str
    input_json: dict[str, Any] | None
    output_json: dict[str, Any] | None
    error_message: str | None
    latency_ms: int
    created_at: datetime


class QuickApplyRequest(BaseModel):
    profile_id: int
    job_id: int
    resume_version_id: int | None = None
    browser_assist: bool = False


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    job_id: int
    resume_version_id: int | None
    status: str
    apply_url: str | None
    cover_letter: str | None
    outreach_message: str | None
    checklist_json: list[str]
    automation_result_json: dict[str, Any] | None
    created_at: datetime
