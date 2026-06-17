from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.entities import Job, Profile, ResumeVersion


def test_profile_and_resume_html_preview_routes(db_session):
    profile = Profile(
        name="李明",
        email="liming@example.com",
        phone="13800000000",
        headline="Agent 开发实习生候选人",
        target_roles_json=["Agent 开发实习生"],
        source_type="guided",
        raw_resume_text="CareerAgent with Python FastAPI RAG SQLite.",
        structured_profile_json={
            "name": "李明",
            "email": "liming@example.com",
            "phone": "13800000000",
            "headline": "Agent 开发实习生候选人",
            "target_roles": ["Agent 开发实习生"],
            "skills": ["Python", "FastAPI", "RAG", "SQLite"],
            "projects": [
                {
                    "name": "CareerAgent",
                    "description": "构建真实可用的求职助手 Agent。",
                    "tech_stack": ["Python", "FastAPI"],
                    "impact": "跑通简历定制、投递包和面试准备流程。",
                }
            ],
            "raw_text": "CareerAgent with Python FastAPI RAG SQLite.",
        },
    )
    job = Job(
        source="manual",
        external_id="preview-job",
        title="Agent 开发实习生",
        raw_jd_text="负责 Agent workflow、RAG 和 FastAPI。",
        structured_jd_json={"required_skills": ["FastAPI", "RAG"]},
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)
    version = ResumeVersion(
        profile_id=profile.id,
        job_id=job.id,
        title="李明 - Agent 开发实习生",
        tailored_resume_markdown="# 李明\n\n## 项目经历\n- **CareerAgent**：构建求职助手 Agent。",
        change_summary_json=[{"summary": "突出 Agent 项目"}],
        keyword_alignment_json={"covered": ["FastAPI", "RAG"], "missing": ["Tool Calling"]},
        source_evidence_json=[],
        verification_json={"passed": True, "risk_level": "low"},
    )
    db_session.add(version)
    db_session.commit()
    db_session.refresh(version)

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        profile_response = client.get(f"/profiles/{profile.id}/html")
        resume_response = client.get(f"/resumes/{version.id}/html")
    finally:
        app.dependency_overrides.clear()

    assert profile_response.status_code == 200
    assert "text/html" in profile_response.headers["content-type"]
    assert "李明" in profile_response.text
    assert "CareerAgent" in profile_response.text
    assert "打印 / 另存为 PDF" in profile_response.text

    assert resume_response.status_code == 200
    assert "text/html" in resume_response.headers["content-type"]
    assert "<h1>李明</h1>" in resume_response.text
    assert "<strong>CareerAgent</strong>" in resume_response.text
    assert "待补关键词" in resume_response.text
