from app.models.entities import Job, Profile
from app.services.matcher import MatcherService
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


def test_matcher_scores_agent_job(db_session):
    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Python FastAPI RAG Agent SQLite evaluation tool calling",
        structured_profile_json={
            "skills": ["Python", "FastAPI", "RAG", "Agent", "SQLite", "Evaluation"],
            "projects": [{"name": "CareerAgent", "description": "RAG Agent workflow", "tech_stack": ["FastAPI"]}],
            "raw_text": "Python FastAPI RAG Agent SQLite evaluation tool calling",
        },
    )
    job = Job(
        source="manual",
        external_id="job-1",
        title="Agent Development Intern",
        company="Tencent",
        raw_jd_text="Build Agent systems with FastAPI, RAG, SQLite and evaluation.",
        structured_jd_json={
            "required_skills": ["FastAPI", "RAG", "SQLite"],
            "preferred_skills": ["Evaluation"],
            "keywords": ["Agent", "tool calling"],
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

    payload = MatcherService().build_match_payload(db_session, profile, job)

    assert payload["overall_score"] >= 60
    assert set(payload["matched_skills"]) >= {"FastAPI", "RAG", "SQLite"}
