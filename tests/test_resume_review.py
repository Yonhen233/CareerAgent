import asyncio

from app.models.entities import Job, Profile, ResumeChunk
from app.services.resume_review import ResumeReviewService


def _profile(**overrides):
    data = {
        "name": "李明",
        "email": "liming@example.com",
        "phone": "13800000000",
        "headline": "Agent 开发实习生候选人",
        "target_roles_json": ["Agent 开发实习生"],
        "source_type": "guided",
        "raw_resume_text": (
            "李明 Agent 开发实习生候选人\n"
            "CareerAgent 项目：实现 PDF Chunk、SQLite RAG、LangGraph workflow、LLM trace 和 Guardrails。"
            "构建 120 条噪声评测样本，统计 recall、false positive rate 和端到端耗时。"
        ),
        "structured_profile_json": {
            "name": "李明",
            "email": "liming@example.com",
            "phone": "13800000000",
            "headline": "Agent 开发实习生候选人",
            "target_roles": ["Agent 开发实习生"],
            "education": [{"school": "XX大学", "degree": "本科", "major": "计算机科学", "duration": "2023-2027"}],
            "skills": ["Python", "FastAPI", "SQLite", "RAG", "LangGraph", "Guardrails"],
            "projects": [
                {
                    "name": "CareerAgent",
                    "description": "实现面向中文求职场景的 Agent 求职助手，包含 PDF Chunk、RAG、岗位匹配和流程追踪。",
                    "tech_stack": ["Python", "FastAPI", "SQLite", "RAG", "LangGraph"],
                    "impact": "构建 120 条评测样本，量化 RAG recall 和 prompt injection false positive rate。",
                }
            ],
            "work_experience": [],
            "raw_text": "CareerAgent 项目实现 PDF Chunk、SQLite RAG、LangGraph workflow 和 LLM trace。",
        },
    }
    data.update(overrides)
    return Profile(**data)


def test_resume_review_scores_general_profile(db_session):
    profile = _profile()
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    review = asyncio.run(ResumeReviewService().review_profile(db_session, profile=profile, include_llm=False))

    assert review["review_type"] == "general"
    assert review["overall_score"] >= 70
    assert review["dimension_scores"]["evidence_strength"] > 60
    assert review["suggestions"]
    assert review["trace"]["rag_used"] is False


def test_resume_review_uses_rag_for_targeted_job(db_session):
    profile = _profile()
    job = Job(
        source="manual",
        external_id="job-agent-rag",
        title="Agent 开发实习生",
        company="测试科技",
        location="深圳",
        job_type="实习",
        apply_url=None,
        raw_jd_text="负责 Agent workflow、FastAPI、RAG、SQLite、LangGraph、Guardrails 和 LLM evaluation。",
        structured_jd_json={
            "required_skills": ["FastAPI", "RAG", "SQLite", "LangGraph", "Guardrails"],
            "preferred_skills": ["LLM evaluation"],
            "responsibilities": ["开发 Agent workflow", "建设 RAG 评测"],
            "keywords": ["Agent", "RAG", "LangGraph"],
        },
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)

    review = asyncio.run(
        ResumeReviewService().review_profile(db_session, profile=profile, job=job, include_llm=False)
    )

    assert review["review_type"] == "targeted"
    assert review["target_alignment"]["match_score"] > 60
    assert review["dimension_scores"]["target_alignment"] == review["target_alignment"]["match_score"]
    assert review["trace"]["rag_used"] is True
    assert review["rag_evidence"]
    assert db_session.query(ResumeChunk).filter(ResumeChunk.profile_id == profile.id).count() > 0
