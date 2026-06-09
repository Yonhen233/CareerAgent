import asyncio

from app.agents.orchestrator import AgentOrchestrator
from app.models.entities import Job, Profile
from app.models.schemas import AgentRunRequest
from app.services.interview_delivery import InterviewPrepDeliveryService
from app.services.interview_experience import InterviewExperienceService
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


def test_interview_experience_import_extracts_questions_topics_and_credibility(db_session):
    raw_text = (
        "牛客网 腾讯 Agent 开发实习一面。"
        "一面：面试官问 RAG 召回率怎么评估？"
        "追问：如果 FastAPI 接口并发变高，你会怎么定位瓶颈？"
        "二面：问 SQLite 存储 trace 和向量检索元数据有什么边界？"
    )

    row = InterviewExperienceService().create_experience(
        db_session,
        source_site="nowcoder",
        source_url="https://www.nowcoder.com/discuss/example",
        title="腾讯 Agent 开发实习一面",
        company="腾讯",
        role_keyword="Agent 开发实习生",
        raw_text=raw_text,
    )

    assert row.source_site == "牛客网"
    assert len(row.extracted_questions_json) >= 3
    assert {"RAG", "FastAPI", "SQLite"} <= set(row.topics_json)
    assert row.credibility_json["score"] >= 0.75
    assert row.credibility_json["has_url"] is True


def test_interview_prep_uses_imported_source_backed_experience_questions(db_session):
    profile, job = _seed_profile_job(db_session)
    experience = InterviewExperienceService().create_experience(
        db_session,
        job=job,
        source_site="牛客网",
        source_url="https://www.nowcoder.com/discuss/source-backed-agent",
        title="腾讯 Agent 实习面经",
        raw_text=(
            "一面：面试官问 RAG 的 chunk 切分策略怎么选？"
            "追问：FastAPI 并发接口如何记录 trace？"
            "二面：如果 MLflow 没有生产经验，你怎么诚实说明？"
        ),
    )

    prep = InterviewPrepService().create_interview_prep(
        db_session,
        profile=profile,
        job=job,
        experience_ids=[experience.id],
    )
    source_questions = [
        question
        for group in prep.question_sets_json
        for question in group.get("questions", [])
        if question.get("source_perspective") == "source_backed_interview_experience"
    ]

    assert prep.summary_json["interview_experience_source_count"] == 1
    assert prep.coverage_json["research_mode"] == "source_backed_and_checklist"
    assert prep.coverage_json["source_backed_question_count"] >= 2
    assert source_questions
    assert any(ref.get("source_site") == "牛客网" for question in source_questions for ref in question["evidence_refs"])


def test_interview_prep_delivery_exports_markdown_and_tracks_practice(db_session):
    profile, job = _seed_profile_job(db_session)
    prep = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=job)
    delivery = InterviewPrepDeliveryService()
    questions = delivery.question_items(prep)

    assert questions
    assert all(item["question_id"] for item in questions)
    source_summary = delivery.source_perspective_summary(prep)
    assert source_summary["core_perspectives"]["online_experience"] > 0
    assert source_summary["core_perspectives"]["resume_project_stack"] > 0
    assert source_summary["core_perspectives"]["other_interview_questions"] > 0

    first_question_id = questions[0]["question_id"]
    row = delivery.upsert_practice_item(
        db_session,
        prep,
        question_id=first_question_id,
        status="ready",
        confidence_score=4,
        notes="已按项目背景、行动、指标准备 90 秒回答。",
    )
    summary = delivery.progress_summary(prep, [row])
    markdown = delivery.render_markdown(prep, practice_items=[row])

    assert row.status == "ready"
    assert summary["ready_count"] == 1
    assert summary["ready_rate"] > 0
    assert first_question_id in markdown
    assert "状态：ready" in markdown
    assert "信心：4/5" in markdown
    assert "问题来源分布" in markdown
    assert "牛客/OfferShow/小红书调研" in markdown
    assert "简历项目技术栈" in markdown
    assert "证据边界" in markdown


def test_interview_practice_rejects_unknown_question_id(db_session):
    profile, job = _seed_profile_job(db_session)
    prep = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=job)

    try:
        InterviewPrepDeliveryService().upsert_practice_item(
            db_session,
            prep,
            question_id="q99_99",
            status="ready",
            confidence_score=5,
        )
    except ValueError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("unknown question_id should be rejected")
