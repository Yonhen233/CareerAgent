from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


def empty_string_when_missing(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def string_list_when_missing(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


class EducationItem(BaseModel):
    school: str = ""
    degree: str = ""
    major: str = ""
    duration: str = ""
    details: str = ""

    _normalize_strings = field_validator("school", "degree", "major", "duration", "details", mode="before")(
        empty_string_when_missing
    )


class ProjectItem(BaseModel):
    name: str = ""
    description: str = ""
    tech_stack: list[str] = Field(default_factory=list)
    impact: str = ""

    _normalize_strings = field_validator("name", "description", "impact", mode="before")(empty_string_when_missing)
    _normalize_lists = field_validator("tech_stack", mode="before")(string_list_when_missing)


class ExperienceItem(BaseModel):
    company: str = ""
    role: str = ""
    duration: str = ""
    details: str = ""
    tech_stack: list[str] = Field(default_factory=list)

    _normalize_strings = field_validator("company", "role", "duration", "details", mode="before")(
        empty_string_when_missing
    )
    _normalize_lists = field_validator("tech_stack", mode="before")(string_list_when_missing)


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

    _normalize_raw_text = field_validator("raw_text", mode="before")(empty_string_when_missing)
    _normalize_lists = field_validator(
        "target_roles",
        "skills",
        "education",
        "projects",
        "work_experience",
        "awards",
        "languages",
        mode="before",
    )(lambda value: [] if value is None else value)


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

    _normalize_lists = field_validator(
        "target_roles",
        "education",
        "skills",
        "projects",
        "work_experience",
        "awards",
        "languages",
        mode="before",
    )(lambda value: [] if value is None else value)


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

    _normalize_lists = field_validator(
        "required_skills",
        "preferred_skills",
        "responsibilities",
        "qualifications",
        "keywords",
        mode="before",
    )(string_list_when_missing)


class JobCreateRequest(BaseModel):
    jd_text: str = Field(min_length=20)
    title: str | None = None
    company: str | None = None
    location: str | None = None
    apply_url: HttpUrl | None = None


class JobSearchRequest(BaseModel):
    query: str = "Agent 开发实习生"
    location: str | None = None
    internship_only: bool = True
    limit: int = Field(default=20, ge=1, le=80)
    sources: list[str] = Field(default_factory=lambda: ["tencent"])
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


class JobChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    chunk_uid: str
    chunk_type: str
    source: str
    text: str
    token_count: int
    metadata_json: dict[str, Any]
    created_at: datetime


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
    task_type: Literal["find_jobs_for_profile", "tailor_resume_for_job", "quick_apply", "prepare_interview_for_job"]
    profile_id: int | None = None
    job_id: int | None = None
    resume_version_id: int | None = None
    query: str | None = "Agent 开发实习生"
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


class LLMCallLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trace_name: str
    model: str
    base_url: str
    status: str
    prompt_preview_json: dict[str, Any]
    response_preview: str | None
    error_message: str | None
    latency_ms: int
    prompt_chars: int
    response_chars: int
    created_at: datetime


class EvaluationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    summary_json: dict[str, Any]
    case_results_json: list[dict[str, Any]]
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


class InterviewPrepRequest(BaseModel):
    profile_id: int
    job_id: int


class InterviewPrepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    profile_id: int
    job_id: int
    match_result_id: int | None
    title: str
    summary_json: dict[str, Any]
    question_sets_json: list[dict[str, Any]]
    gap_drills_json: list[dict[str, Any]]
    research_checklist_json: list[dict[str, Any]]
    source_evidence_json: list[dict[str, Any]]
    coverage_json: dict[str, Any]
    generation_mode: str
    created_at: datetime
