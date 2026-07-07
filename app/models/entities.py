from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    headline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_roles_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="pdf")
    raw_resume_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_profile_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    chunks: Mapped[list["ResumeChunk"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    matches: Mapped[list["MatchResult"]] = relationship(back_populates="profile")
    resume_versions: Mapped[list["ResumeVersion"]] = relationship(back_populates="profile")
    applications: Mapped[list["Application"]] = relationship(back_populates="profile")
    interview_preps: Mapped[list["InterviewPrep"]] = relationship(back_populates="profile")
    agent_runs: Mapped[list["AgentRun"]] = relationship(back_populates="profile")


class ResumeChunk(Base):
    __tablename__ = "resume_chunks"
    __table_args__ = (UniqueConstraint("profile_id", "chunk_uid", name="uq_resume_chunk_profile_uid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    chunk_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_json: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="chunks")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_job_source_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    job_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    apply_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_jd_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    matches: Mapped[list["MatchResult"]] = relationship(back_populates="job")
    chunks: Mapped[list["JobChunk"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    resume_versions: Mapped[list["ResumeVersion"]] = relationship(back_populates="job")
    applications: Mapped[list["Application"]] = relationship(back_populates="job")
    interview_preps: Mapped[list["InterviewPrep"]] = relationship(back_populates="job")
    interview_experiences: Mapped[list["InterviewExperience"]] = relationship(back_populates="job")
    agent_runs: Mapped[list["AgentRun"]] = relationship(back_populates="job")


class JobChunk(Base):
    __tablename__ = "job_chunks"
    __table_args__ = (UniqueConstraint("job_id", "chunk_uid", name="uq_job_chunk_job_uid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    chunk_uid: Mapped[str] = mapped_column(String(128), nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_json: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    job: Mapped[Job] = relationship(back_populates="chunks")


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    dimension_scores_json: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    matched_skills_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    missing_skills_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    relevant_evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    suggestions_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="matches")
    job: Mapped[Job] = relationship(back_populates="matches")


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    tailored_resume_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    change_summary_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    keyword_alignment_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    verification_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    diff_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="resume_versions")
    job: Mapped[Job] = relationship(back_populates="resume_versions")
    applications: Mapped[list["Application"]] = relationship(back_populates="resume_version")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    resume_version_id: Mapped[int | None] = mapped_column(ForeignKey("resume_versions.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    apply_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_letter: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checklist_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    automation_result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    profile: Mapped[Profile] = relationship(back_populates="applications")
    job: Mapped[Job] = relationship(back_populates="applications")
    resume_version: Mapped[ResumeVersion | None] = relationship(back_populates="applications")


class InterviewPrep(Base):
    __tablename__ = "interview_preps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    match_result_id: Mapped[int | None] = mapped_column(ForeignKey("match_results.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    question_sets_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    gap_drills_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    research_checklist_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    source_evidence_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    coverage_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    generation_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="structured_rules_v1")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="interview_preps")
    job: Mapped[Job] = relationship(back_populates="interview_preps")
    match_result: Mapped[MatchResult | None] = relationship()
    practice_items: Mapped[list["InterviewPracticeItem"]] = relationship(
        back_populates="interview_prep",
        cascade="all, delete-orphan",
    )


class InterviewPracticeItem(Base):
    __tablename__ = "interview_practice_items"
    __table_args__ = (
        UniqueConstraint("interview_prep_id", "question_id", name="uq_interview_practice_question"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    interview_prep_id: Mapped[int] = mapped_column(ForeignKey("interview_preps.id"), nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="todo", index=True)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    interview_prep: Mapped[InterviewPrep] = relationship(back_populates="practice_items")


class InterviewExperience(Base):
    __tablename__ = "interview_experiences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    source_site: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    role_keyword: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_questions_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    topics_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    rounds_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    credibility_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    job: Mapped[Job | None] = relationship(back_populates="interview_experiences")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id"), nullable=True, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    profile: Mapped[Profile | None] = relationship(back_populates="agent_runs")
    job: Mapped[Job | None] = relationship(back_populates="agent_runs")
    steps: Mapped[list["AgentStep"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    artifacts: Mapped[list["AgentArtifact"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    events: Mapped[list["AgentEvent"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    approvals: Mapped[list["AgentApproval"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="steps")


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    artifact_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="artifacts")


class AgentApproval(Base):
    __tablename__ = "agent_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="approvals")


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    node_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    event_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="events")


class LLMCallLog(Base):
    __tablename__ = "llm_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trace_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    prompt_preview_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    response_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    case_results_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued", index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    progress_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
