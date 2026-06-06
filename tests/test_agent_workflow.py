import asyncio

from app.agents.orchestrator import AgentOrchestrator
from app.models.entities import Job, Profile
from app.models.schemas import AgentRunRequest
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


def test_tailor_resume_agent_workflow(db_session):
    profile = Profile(
        name="Candidate",
        email="candidate@example.com",
        headline="Agent developer",
        source_type="guided",
        raw_resume_text="Candidate built CareerAgent with Python, FastAPI, RAG, SQLite and Agent trace.",
        structured_profile_json={
            "name": "Candidate",
            "email": "candidate@example.com",
            "headline": "Agent developer",
            "skills": ["Python", "FastAPI", "RAG", "SQLite", "Agent"],
            "projects": [
                {
                    "name": "CareerAgent",
                    "description": "Job-search agent with PDF chunking, SQLite RAG, and observable workflow.",
                    "tech_stack": ["Python", "FastAPI", "SQLite"],
                    "impact": "End-to-end usable workflow",
                }
            ],
            "raw_text": "Candidate built CareerAgent with Python, FastAPI, RAG, SQLite and Agent trace.",
        },
    )
    job = Job(
        source="manual",
        external_id="job-1",
        title="Agent Development Intern",
        company="Tencent",
        raw_jd_text="Develop Agent workflows using FastAPI, RAG, SQLite, evaluation and guardrails.",
        structured_jd_json={
            "title": "Agent Development Intern",
            "company": "Tencent",
            "required_skills": ["FastAPI", "RAG", "SQLite"],
            "keywords": ["Agent", "guardrails"],
        },
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)
    SQLiteVectorIndex().upsert_profile_chunks(
        db_session,
        profile.id,
        ResumeTextSplitter().build_resume_chunks(profile.structured_profile_json),
    )

    run = asyncio.run(
        AgentOrchestrator().run(
            db_session,
            AgentRunRequest(task_type="tailor_resume_for_job", profile_id=profile.id, job_id=job.id),
        )
    )

    assert run.status == "completed"
    assert run.output_json["resume_version_id"] > 0
    assert run.output_json["verification"]["risk_level"] in {"low", "medium", "high"}
    assert run.output_json["execution_plan"]["context_policy"]["progressive_disclosure"] is True
    assert not any(item["name"] == "context_manager" for item in run.output_json["execution_plan"]["subagents"])
