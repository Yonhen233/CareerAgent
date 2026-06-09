import asyncio

from app.agents.orchestrator import AgentOrchestrator
from app.models.entities import Job, Profile
from app.models.schemas import AgentRunRequest
from app.services.interview_prep import InterviewPrepService
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


def _seed_profile_job(db_session):
    profile = Profile(
        name="陈同学",
        headline="Agent 开发实习生候选人",
        target_roles_json=["Agent 开发实习生"],
        source_type="guided",
        raw_resume_text=(
            "构建 CareerAgent，使用 Python、FastAPI、SQLite、RAG 和 Agent Trace。"
            "实现 PDF chunk、岗位匹配评测和投递 Guardrail。没有 MLflow 生产经验。"
        ),
        structured_profile_json={
            "name": "陈同学",
            "headline": "Agent 开发实习生候选人",
            "target_roles": ["Agent 开发实习生"],
            "skills": ["Python", "FastAPI", "SQLite", "RAG", "Agent Trace"],
            "projects": [
                {
                    "name": "CareerAgent",
                    "description": "构建中文求职助手 Agent，支持 PDF chunk、RAG 检索、岗位匹配和投递 Guardrail。",
                    "tech_stack": ["Python", "FastAPI", "SQLite", "RAG"],
                    "impact": "用评测集跟踪 JD parser、RAG 和投递包质量。",
                }
            ],
            "raw_text": (
                "构建 CareerAgent，使用 Python、FastAPI、SQLite、RAG 和 Agent Trace。"
                "实现 PDF chunk、岗位匹配评测和投递 Guardrail。没有 MLflow 生产经验。"
            ),
        },
    )
    job = Job(
        source="manual",
        external_id="interview-agent-intern",
        title="Agent 开发实习生",
        company="腾讯",
        location="深圳",
        job_type="实习",
        raw_jd_text=(
            "负责 Agent 应用开发，使用 Python、FastAPI、RAG、SQLite 和评测体系，"
            "了解 MLflow 加分。需要参与需求拆解、效果评估和系统优化。"
        ),
        structured_jd_json={
            "title": "Agent 开发实习生",
            "company": "腾讯",
            "required_skills": ["Python", "FastAPI", "RAG", "SQLite", "MLflow"],
            "preferred_skills": ["Agent Trace", "Evaluation"],
            "responsibilities": ["开发 Agent 应用", "建设 RAG 检索与评测链路", "参与需求拆解和效果评估"],
            "keywords": ["Agent", "Guardrail", "PDF Chunk"],
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
    return profile, job


def test_interview_prep_covers_online_project_and_general_perspectives(db_session):
    profile, job = _seed_profile_job(db_session)

    prep = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=job)

    categories = {group["category"] for group in prep.question_sets_json}
    assert "同岗位面经与高频追问" in categories
    assert "简历项目技术栈追问" in categories
    assert "通用面试与行为问题" in categories
    assert prep.coverage_json["passed"] is True
    assert prep.coverage_json["required_skill_coverage_rate"] == 1.0
    assert prep.coverage_json["missing_skill_drill_rate"] == 1.0
    assert {item["site"] for item in prep.research_checklist_json} >= {"牛客网", "OfferShow", "小红书"}
    assert any(item["skill"] == "MLflow" for item in prep.gap_drills_json)
    assert "不能包装成已交付经验" in prep.summary_json["boundary"]


def test_interview_prep_agent_workflow_records_artifact(db_session):
    profile, job = _seed_profile_job(db_session)

    run = asyncio.run(
        AgentOrchestrator().run(
            db_session,
            AgentRunRequest(task_type="prepare_interview_for_job", profile_id=profile.id, job_id=job.id),
        )
    )

    assert run.status == "completed"
    assert run.output_json["interview_prep_id"] > 0
    assert run.output_json["coverage"]["passed"] is True
    assert run.output_json["execution_plan"]["skills"] == [
        "evidence_retrieval",
        "fit_assessment",
        "interview_preparation",
    ]
