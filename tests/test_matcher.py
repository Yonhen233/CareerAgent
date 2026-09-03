import pytest
from fastapi import HTTPException

from app.api.matches import create_match
from app.models.entities import Job, Profile
from app.models.schemas import MatchCreateRequest
from app.services.matcher import MatcherService, fuzzy_contains
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


def test_matcher_penalizes_negative_or_coursework_only_evidence(db_session):
    profile = Profile(
        name="Beginner",
        source_type="guided",
        raw_resume_text=(
            "Wu Fan\nAgent Development Intern Candidate\nTarget roles: Agent Development Intern\n"
            "Skills: Python basics, HTML, CSS. Coursework: read articles about RAG, Agent, "
            "FastAPI and SQLite. No shipped project, no API service, no evaluation harness."
        ),
        headline="Agent Development Intern Candidate",
        target_roles_json=["Agent Development Intern"],
        structured_profile_json={
            "headline": "Agent Development Intern Candidate",
            "target_roles": ["Agent Development Intern"],
            "skills": ["Python basics", "HTML", "CSS"],
            "projects": [
                {
                    "name": "Course Notes",
                    "description": "Read articles about RAG, Agent, FastAPI and SQLite. No shipped project.",
                    "tech_stack": ["Python basics"],
                }
            ],
            "raw_text": "Read articles about RAG, Agent, FastAPI and SQLite. No shipped project.",
        },
    )
    job = Job(
        source="manual",
        external_id="job-negative",
        title="Agent Development Intern",
        company="Demo AI",
        raw_jd_text="Build Agent systems with FastAPI, RAG, SQLite and evaluation.",
        structured_jd_json={
            "required_skills": ["FastAPI", "RAG", "SQLite", "Agent", "Evaluation"],
            "keywords": ["Agent"],
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

    assert payload["overall_score"] < 55
    assert "Agent" in payload["missing_skills"]
    assert "FastAPI" in payload["missing_skills"]
    assert payload["dimension_scores"]["negative_evidence_penalty"] > 0
    assert any(
        item["evidence_type"] in {"missing_skill_disclosure", "coursework", "planned_learning"}
        for item in payload["relevant_evidence"]
    )


def test_matcher_does_not_treat_machine_learning_as_negative_evidence():
    matcher = MatcherService()

    assert matcher._contains_negative_evidence("built machine learning workflows") is False
    assert matcher._contains_negative_evidence("currently learning RAG from tutorials") is True


def test_matcher_does_not_match_agent_inside_project_name_agenttrace():
    text = "agenttrace: built a trace viewer and rag citation checker"

    assert fuzzy_contains("Agent", {"agenttrace", "trace", "viewer"}, text) is False
    assert fuzzy_contains("Agent", {"agent", "workflow"}, "implemented an agent workflow") is True


def test_matcher_recognizes_vector_search_and_evaluation_delivery_aliases():
    text = (
        "Built Chinese-English hybrid retrieval with BM25, multilingual embeddings and RRF. "
        "Evaluated 320 noisy queries and achieved Recall@10 of 0.89."
    ).lower()
    tokens = set(text.split())

    assert fuzzy_contains("vector search", tokens, text) is True
    assert fuzzy_contains("evaluation", tokens, text) is True
    assert fuzzy_contains("Vector Database", tokens, text) is False


def test_matches_api_returns_structured_error_for_matching_failure(db_session, monkeypatch):
    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Python FastAPI RAG",
        structured_profile_json={"skills": ["Python", "FastAPI", "RAG"], "raw_text": "Python FastAPI RAG"},
    )
    job = Job(
        source="manual",
        external_id="job-error",
        title="Agent Development Intern",
        raw_jd_text="Build Agent systems.",
        structured_jd_json={"required_skills": ["Agent"]},
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)

    class BrokenMatcher:
        def create_match_result(self, db, profile, job):  # noqa: ANN001
            raise ValueError("Unsupported reranker provider: keyword")

    monkeypatch.setattr("app.api.matches.MatcherService", BrokenMatcher)

    with pytest.raises(HTTPException) as exc_info:
        create_match(MatchCreateRequest(profile_id=profile.id, job_id=job.id), db_session)

    assert exc_info.value.status_code == 500
    assert "Match generation failed" in exc_info.value.detail
    assert "Unsupported reranker provider: keyword" in exc_info.value.detail
