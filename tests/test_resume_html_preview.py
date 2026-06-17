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
            "photo_data_url": "data:image/png;base64,iVBORw0KGgo=",
            "location": "深圳",
            "availability": "2026 年暑期可实习",
            "headline": "Agent 开发实习生候选人",
            "self_summary": "熟悉 RAG、FastAPI 和 Agent 工作流，有真实求职助手项目经验。",
            "target_roles": ["Agent 开发实习生"],
            "education": [
                {
                    "school": "南方科技大学",
                    "degree": "本科",
                    "major": "计算机科学与技术",
                    "duration": "2023.09 - 2027.06",
                    "details": "核心课程：数据结构、数据库、机器学习。",
                }
            ],
            "skills": ["Python", "FastAPI", "RAG", "SQLite"],
            "projects": [
                {
                    "name": "CareerAgent",
                    "description": "构建真实可用的求职助手 Agent。",
                    "tech_stack": ["Python", "FastAPI"],
                    "impact": "跑通简历定制、投递包和面试准备流程。",
                }
            ],
            "campus_experience": [
                {
                    "company": "AI 社团",
                    "role": "技术组成员",
                    "duration": "2024.09 - 2025.01",
                    "details": "组织 Agent 分享并维护示例项目。",
                    "tech_stack": [],
                }
            ],
            "certifications": ["英语六级"],
            "awards": ["校级二等奖学金"],
            "languages": ["中文", "英语 CET-6"],
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
    assert "个人总结" in profile_response.text
    assert "教育经历" in profile_response.text
    assert "校园/实践经历" in profile_response.text
    assert "证书" in profile_response.text
    assert 'class="resume-photo"' in profile_response.text
    assert "data:image/png;base64,iVBORw0KGgo=" in profile_response.text
    assert "打印 / 另存为 PDF" in profile_response.text

    assert resume_response.status_code == 200
    assert "text/html" in resume_response.headers["content-type"]
    assert "<h1>李明</h1>" in resume_response.text
    assert "<strong>CareerAgent</strong>" in resume_response.text
    assert "待补关键词" in resume_response.text
