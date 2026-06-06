import asyncio

from app.agents.orchestrator import AgentOrchestrator
from app.models.entities import Job, Profile
from app.models.schemas import AgentRunRequest
from app.services.resume_tailor import ResumeTailorService
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


def test_tailor_resume_repairs_high_risk_draft_once(db_session, monkeypatch):
    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Built model evaluation dashboards with Python and PyTorch. No MLflow experience.",
        structured_profile_json={
            "skills": ["Python", "PyTorch", "Evaluation"],
            "projects": [
                {
                    "name": "VisionBench",
                    "description": "Built model evaluation dashboards with Python and PyTorch.",
                    "tech_stack": ["Python", "PyTorch"],
                }
            ],
            "raw_text": "Built model evaluation dashboards with Python and PyTorch. No MLflow experience.",
        },
    )
    job = Job(
        source="manual",
        external_id="job-repair",
        title="Machine Learning Platform Intern",
        company="MLWorks",
        raw_jd_text="Maintain MLflow, PyTorch baselines and model evaluation dashboards.",
        structured_jd_json={"required_skills": ["Python", "PyTorch", "MLflow", "Model Evaluation"]},
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

    service = ResumeTailorService()

    def risky_draft(profile, job, evidence):
        return {
            "tailored_resume_markdown": (
                "Built model evaluation dashboards with Python and PyTorch.\n"
                "Eager to learn MLflow for platform workflows."
            ),
            "change_summary": [],
            "keyword_alignment": {"covered": ["Python", "PyTorch"], "missing": ["MLflow"], "notes": []},
        }

    monkeypatch.setattr(service, "_heuristic_tailor", risky_draft)
    monkeypatch.setattr(type(service.llm), "available", property(lambda self: False))

    version = asyncio.run(service.tailor_resume(db_session, profile, job))

    assert "MLflow" not in version.tailored_resume_markdown
    assert version.verification_json["passed"] is True
    repair = version.keyword_alignment_json["react_repair"]
    assert repair["attempted"] is True
    assert repair["attempts"][0]["after_passed"] is True
