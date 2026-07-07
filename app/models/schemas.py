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
    photo_data_url: str | None = None
    location: str | None = None
    availability: str | None = None
    headline: str | None = None
    self_summary: str | None = None
    enabled_sections: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    work_experience: list[ExperienceItem] = Field(default_factory=list)
    campus_experience: list[ExperienceItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    portfolio_links: list[str] = Field(default_factory=list)
    prompt_injection: dict[str, Any] = Field(default_factory=dict)
    raw_text: str = ""

    _normalize_raw_text = field_validator("raw_text", mode="before")(empty_string_when_missing)
    _normalize_optional_strings = field_validator(
        "photo_data_url", "location", "availability", "self_summary", mode="before"
    )(lambda value: None if value is None else str(value))
    _normalize_lists = field_validator(
        "enabled_sections",
        "target_roles",
        "skills",
        "education",
        "projects",
        "work_experience",
        "campus_experience",
        "certifications",
        "awards",
        "languages",
        "portfolio_links",
        mode="before",
    )(lambda value: [] if value is None else value)


class GuidedProfileRequest(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    photo_data_url: str | None = None
    location: str | None = None
    availability: str | None = None
    headline: str | None = "Agent 开发实习生候选人"
    self_summary: str | None = None
    enabled_sections: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=lambda: ["Agent 开发实习生"])
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    work_experience: list[ExperienceItem] = Field(default_factory=list)
    campus_experience: list[ExperienceItem] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    portfolio_links: list[str] = Field(default_factory=list)

    _normalize_lists = field_validator(
        "enabled_sections",
        "target_roles",
        "education",
        "skills",
        "projects",
        "work_experience",
        "campus_experience",
        "certifications",
        "awards",
        "languages",
        "portfolio_links",
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
    prompt_injection: dict[str, Any] = Field(default_factory=dict)

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
    idempotency_key: str | None = None
    created_at: datetime


class AgentRunRequest(BaseModel):
    task_type: Literal[
        "find_jobs_for_profile",
        "tailor_resume_for_job",
        "quick_apply",
        "prepare_interview_for_job",
        "full_career_flow",
    ]
    profile_id: int | None = None
    job_id: int | None = None
    resume_version_id: int | None = None
    query: str | None = "Agent 开发实习生"
    location: str | None = None
    limit: int = Field(default=20, ge=1, le=80)
    application_confirmed: bool = False


class AgentRunResumeRequest(BaseModel):
    confirmed: bool = True
    note: str | None = None
    resume_json: dict[str, Any] = Field(default_factory=dict)


class AgentRunCancelRequest(BaseModel):
    reason: str | None = None


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


class AgentApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    action_type: str
    status: str
    payload_hash: str
    payload_summary_json: dict[str, Any]
    note: str | None
    decided_by_user_id: str | None
    created_at: datetime
    decided_at: datetime | None


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


class AgentEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    event_type: str
    node_name: str | None
    event_json: dict[str, Any]
    created_at: datetime


class NaturalLanguageAgentRequest(BaseModel):
    instruction: str = Field(min_length=4)
    profile_id: int | None = None
    job_id: int | None = None
    resume_version_id: int | None = None
    jd_text: str | None = None
    query: str | None = "Agent 开发实习生"
    location: str | None = None
    limit: int = Field(default=8, ge=1, le=30)


class NaturalLanguageAgentResponse(BaseModel):
    run_id: int
    status: str
    user_message: str
    plan_json: dict[str, Any]
    result_json: dict[str, Any]
    repair_attempts: list[dict[str, Any]] = Field(default_factory=list)


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
    context_json: dict[str, Any]
    created_at: datetime


class EvaluationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    summary_json: dict[str, Any]
    case_results_json: list[dict[str, Any]]
    created_at: datetime


class TaskRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_type: str
    status: str
    input_json: dict[str, Any]
    progress_json: dict[str, Any]
    output_json: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


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
    idempotency_key: str | None = None
    created_at: datetime


class InterviewPrepRequest(BaseModel):
    profile_id: int
    job_id: int
    experience_ids: list[int] | None = None


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
    idempotency_key: str | None = None
    created_at: datetime


class InterviewPracticeItemUpdateRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=128)
    status: Literal["todo", "practicing", "ready", "deferred"] = "todo"
    confidence_score: int = Field(default=0, ge=0, le=5)
    notes: str | None = None


class InterviewPracticeItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    interview_prep_id: int
    question_id: str
    status: str
    confidence_score: int
    notes: str | None
    created_at: datetime
    updated_at: datetime


class InterviewExperienceCreateRequest(BaseModel):
    job_id: int | None = None
    source_site: str = Field(min_length=1, max_length=80)
    source_url: str | None = None
    title: str | None = None
    company: str | None = None
    role_keyword: str | None = None
    raw_text: str = Field(min_length=20)


class InterviewExperienceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int | None
    source_site: str
    source_url: str | None
    title: str | None
    company: str | None
    role_keyword: str | None
    raw_text: str
    extracted_questions_json: list[dict[str, Any]]
    topics_json: list[str]
    rounds_json: list[str]
    credibility_json: dict[str, Any]
    created_at: datetime
