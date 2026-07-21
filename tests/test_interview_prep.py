import asyncio
import json

from app.agents.orchestrator import AgentOrchestrator
from app.api.interview_prep import _interview_prep_response, list_interview_preps
from app.models.entities import Job, Profile
from app.models.schemas import AgentRunRequest
from app.services.interview_delivery import InterviewPrepDeliveryService
from app.services.interview_experience import InterviewExperienceService
from app.services.interview_prep import InterviewPrepService
from app.services.interview_references import InterviewReferenceService
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
    questions = [question for group in prep.question_sets_json for question in group.get("questions", [])]
    preparation_angles = {item["angle"]: item for item in prep.summary_json["preparation_angles"]}
    assert "同岗位面经与高频追问" in categories
    assert "简历项目技术栈追问" in categories
    assert "通用面试与行为问题" in categories
    assert set(preparation_angles) == {
        "same_role_interview_experience",
        "resume_project_tech_stack",
        "other_possible_interview_questions",
    }
    assert all(item["question_count"] > 0 for item in preparation_angles.values())
    assert {question["preparation_angle"] for question in questions} >= set(preparation_angles)
    assert prep.coverage_json["passed"] is True
    assert prep.coverage_json["preparation_angles_passed"] is True
    assert prep.coverage_json["question_quality_passed"] is True
    assert prep.summary_json["question_quality"]["passed"] is True
    assert prep.summary_json["question_quality"]["score"] >= 0.82
    assert prep.coverage_json["required_skill_coverage_rate"] == 1.0
    assert prep.coverage_json["missing_skill_drill_rate"] == 1.0
    assert {item["site"] for item in prep.research_checklist_json} >= {"牛客网", "OfferShow", "小红书"}
    assert any(item["skill"] == "MLflow" for item in prep.gap_drills_json)
    assert "不能包装成已交付经验" in prep.summary_json["boundary"]
    assert all(len(question.get("follow_ups") or []) >= 2 for question in questions)


def test_interview_prep_quality_judge_flags_weak_questions(db_session):
    profile, job = _seed_profile_job(db_session)
    service = InterviewPrepService()
    weak_question_sets = [
        {
            "category": "弱题样例",
            "questions": [
                {
                    "question_id": "weak_1",
                    "question": "介绍一下你自己。",
                    "source_perspective": "general_interview",
                    "risk_level": "low",
                    "skills": [],
                    "answer_points": [],
                    "follow_ups": [],
                }
            ],
        }
    ]

    quality = service._question_quality_judge(
        profile=profile,
        job=job,
        question_sets=weak_question_sets,
        required=["Python", "FastAPI", "RAG", "SQLite"],
        preferred=["Agent Trace"],
        keywords=["Agent", "Guardrail"],
        missing=["MLflow"],
        evidence=[],
    )

    assert quality["passed"] is False
    assert quality["score"] < quality["thresholds"]["score"]
    assert quality["issue_counts"]["jd_alignment"] >= 1
    assert quality["issue_counts"]["follow_up_depth"] >= 1


def test_interview_prep_dedupes_questions_before_quality_gate(db_session):
    profile, job = _seed_profile_job(db_session)
    duplicate_question = {
        "question": "CareerAgent 的 RAG 检索从 query 到 evidence 的链路怎么设计？",
        "follow_ups": ["Top20 为什么需要 reranker？", "SQLite 和向量库如何分工？"],
        "intent": "验证项目实现细节。",
        "answer_points": ["讲 chunk", "讲检索权重", "讲 trace"],
        "skills": ["RAG", "SQLite"],
        "risk_level": "low",
        "source_perspective": "llm_project_implementation",
    }

    prep = InterviewPrepService().create_interview_prep(
        db_session,
        profile=profile,
        job=job,
        llm_question_sets=[
            {"category": "LLM 重复题组 A", "questions": [dict(duplicate_question), dict(duplicate_question)]},
            {"category": "LLM 重复题组 B", "questions": [dict(duplicate_question)]},
        ],
    )

    normalized = [
        InterviewPrepService()._normalize_question_text(question["question"])
        for group in prep.question_sets_json
        for question in group.get("questions", [])
    ]
    assert len(normalized) == len(set(normalized))
    assert prep.coverage_json["question_quality_rates"]["duplicate_rate"] == 0.0
    assert prep.coverage_json["question_quality_passed"] is True


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
    assert run.output_json["summary"]["llm_question_generation"]["enabled"] is True
    assert run.output_json["execution_plan"]["skills"] == [
        "evidence_retrieval",
        "fit_assessment",
        "interview_preparation",
    ]


def test_interview_prep_with_llm_generates_project_and_foundation_followups(db_session):
    profile, job = _seed_profile_job(db_session)

    class FakeInterviewLLM:
        @property
        def available(self):
            return True

        async def generate_text(
            self,
            *,
            system_prompt,
            user_prompt,
            temperature,
            max_tokens,
            response_format=None,
            db,
            trace_name,
        ):
            assert trace_name == "interview_prep.generate_interviewer_questions"
            assert max_tokens == 1200
            assert response_format == {"type": "json_object"}
            assert "CareerAgent" in user_prompt
            assert "Agent 开发实习生" in user_prompt
            return json.dumps({
                "question_sets": [
                    {
                        "category": "LLM 项目实现追问",
                        "questions": [
                            {
                                "question": "CareerAgent 的 RAG 检索从 query 到 evidence 的链路怎么设计？",
                                "follow_ups": ["Top20 为什么需要 reranker？", "SQLite 和向量库如何分工？"],
                                "intent": "验证项目实现细节。",
                                "answer_points": ["讲 chunk", "讲检索权重", "讲 trace"],
                                "skills": ["RAG", "SQLite"],
                                "risk_level": "low",
                                "source_perspective": "llm_project_implementation",
                            },
                            {
                                "question": "FastAPI 接口并发请求下如何记录 LLM trace？",
                                "follow_ups": ["失败时如何恢复？", "如何避免日志泄露 key？"],
                                "intent": "验证工程落地。",
                                "answer_points": ["讲请求边界", "讲日志字段", "讲脱敏"],
                                "skills": ["FastAPI", "Agent Trace"],
                                "risk_level": "low",
                                "source_perspective": "llm_project_implementation",
                            },
                            {
                                "question": "PDF chunk 策略为什么不只按固定长度切？",
                                "follow_ups": ["页级信息怎么保留？", "噪声 chunk 怎么处理？"],
                                "intent": "验证 chunk 设计。",
                                "answer_points": ["讲结构化字段", "讲滑窗", "讲评测"],
                                "skills": ["PDF Chunk", "RAG"],
                                "risk_level": "low",
                                "source_perspective": "llm_project_implementation",
                            },
                        ],
                    },
                    {
                        "category": "LLM 八股与基础追问",
                        "questions": [
                            {
                                "question": "RAG 的召回率和答案质量分别怎么评估？",
                                "follow_ups": ["如何构造负例？", "如何判断 evidence 是否支持回答？"],
                                "intent": "覆盖基础八股。",
                                "answer_points": ["召回", "精排", "人工标注"],
                                "skills": ["RAG", "Evaluation"],
                                "risk_level": "medium",
                                "source_perspective": "llm_foundation_drill",
                            },
                            {
                                "question": "FastAPI 的异步接口适合解决什么问题？",
                                "follow_ups": ["CPU 密集任务怎么办？", "数据库 session 如何管理？"],
                                "intent": "覆盖后端基础。",
                                "answer_points": ["IO 并发", "任务拆分", "连接管理"],
                                "skills": ["FastAPI"],
                                "risk_level": "medium",
                                "source_perspective": "llm_foundation_drill",
                            },
                            {
                                "question": "如果 MLflow 没有生产经验，面试时如何诚实说明？",
                                "follow_ups": ["相邻经验是什么？", "三天内如何补一个 demo？"],
                                "intent": "验证缺口披露。",
                                "answer_points": ["承认缺口", "迁移评测经验", "给补齐计划"],
                                "skills": ["MLflow"],
                                "risk_level": "high",
                                "source_perspective": "llm_foundation_drill",
                            },
                        ],
                    },
                ]
            }, ensure_ascii=False)

    prep = asyncio.run(
        InterviewPrepService(llm=FakeInterviewLLM()).create_interview_prep_with_llm(
            db_session,
            profile=profile,
            job=job,
        )
    )
    questions = [question for group in prep.question_sets_json for question in group.get("questions", [])]
    sources = {question["source_perspective"] for question in questions}
    markdown = InterviewPrepDeliveryService().render_markdown(prep)

    assert prep.generation_mode == "llm_augmented_v1_jd_project_questions"
    assert prep.summary_json["llm_question_generation"]["enabled"] is True
    assert {"llm_project_implementation", "llm_foundation_drill"} <= sources
    assert any(question.get("follow_ups") for question in questions)
    assert prep.coverage_json["preparation_angle_counts"]["resume_project_tech_stack"] >= 3
    assert prep.coverage_json["preparation_angle_counts"]["other_possible_interview_questions"] >= 3
    assert "连续追问" in markdown
    assert "面经参考链接" in markdown


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
        source_url="https://www.nowcoder.com/discuss/123456789",
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


def test_interview_references_filter_placeholders_and_build_honest_search_entries():
    links = InterviewReferenceService.normalize_links(
        [
            {
                "kind": "confirmed_imported_interview_experience",
                "site": "牛客网",
                "title": "示例面经",
                "url": "https://www.nowcoder.com/discuss/example-agent-intern",
            },
            {
                "kind": "confirmed_imported_interview_experience",
                "site": "牛客网",
                "title": "真实导入面经",
                "url": "https://www.nowcoder.com/discuss/123456789",
            },
            {
                "kind": "search_reference_link",
                "site": "牛客网",
                "topic": "同岗位面经",
                "query": "site:nowcoder.com Agent 开发实习生 面经",
                "url": "https://www.baidu.com/s?wd=old",
            },
            {
                "kind": "search_reference_link",
                "site": "小红书",
                "topic": "候选人经验",
                "query": "小红书 Agent 开发实习生 面经",
            },
            {
                "kind": "search_reference_link",
                "site": "OfferShow",
                "topic": "薪资与流程",
                "query": "Agent 开发实习生 offer",
            },
        ]
    )

    assert len(links) == 4
    assert all("example-agent-intern" not in str(item.get("url")) for item in links)
    assert links[0]["reference_type"] == "source_article"
    assert any(item["url"].startswith("https://www.nowcoder.com/search/all?query=") for item in links)
    assert any(item["url"].startswith("https://www.xiaohongshu.com/search_result?keyword=") for item in links)
    assert any(item["url"] == "https://offershow.cn/" for item in links)
    assert {item["reference_type_label"] for item in links} >= {"原文", "搜索入口", "平台入口"}
    assert InterviewReferenceService.normalize_links(links) == links


def test_legacy_interview_links_are_normalized_in_api_and_markdown(db_session):
    profile, job = _seed_profile_job(db_session)
    prep = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=job)
    summary = dict(prep.summary_json or {})
    summary["interview_reference_links"] = [
        {
            "kind": "confirmed_imported_interview_experience",
            "site": "牛客网",
            "title": "旧示例面经",
            "url": "https://www.nowcoder.com/discuss/example-agent-intern",
        },
        {
            "kind": "search_reference_link",
            "site": "牛客网",
            "title": "牛客网：同岗位面经",
            "query": "site:nowcoder.com Agent 开发实习生 面经",
            "url": "https://www.baidu.com/s?wd=legacy",
        },
    ]
    prep.summary_json = summary

    response = _interview_prep_response(prep)
    response_links = response.summary_json["interview_reference_links"]
    markdown = InterviewPrepDeliveryService().render_markdown(prep)

    assert len(response_links) == 1
    assert response_links[0]["title"] == "搜索牛客网：同岗位面经"
    assert response_links[0]["url"].startswith("https://www.nowcoder.com/search/all?query=")
    assert "example-agent-intern" not in markdown
    assert "https://www.nowcoder.com/search/all?query=" in markdown


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
    assert source_summary["preparation_angle_counts"]["same_role_interview_experience"] > 0
    assert source_summary["preparation_angle_counts"]["resume_project_tech_stack"] > 0
    assert source_summary["preparation_angle_counts"]["other_possible_interview_questions"] > 0

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
    assert "面经参考链接" in markdown
    assert "准备角度" in markdown
    assert "网上同岗位面经" in markdown
    assert "牛客/OfferShow/小红书调研" in markdown
    assert "简历项目技术栈" in markdown
    assert "其他可能面试问题" in markdown
    assert "证据边界" in markdown

    updated = delivery.upsert_practice_item(
        db_session,
        prep,
        question_id=first_question_id,
        status="practicing",
        confidence_score=2,
        notes=None,
    )
    assert updated.notes == "已按项目背景、行动、指标准备 90 秒回答。"


def test_interview_prep_list_filters_by_profile_and_job(db_session):
    profile, job = _seed_profile_job(db_session)
    first = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=job)
    other_job = Job(
        source="manual",
        external_id="interview-agent-platform",
        title="Agent 平台开发实习生",
        company="阿里巴巴",
        location="杭州",
        job_type="实习",
        raw_jd_text="负责 Agent 平台、Python、RAG 与评测系统建设。",
        structured_jd_json={
            "title": "Agent 平台开发实习生",
            "company": "阿里巴巴",
            "required_skills": ["Python", "RAG", "Agent"],
            "responsibilities": ["建设 Agent 平台和评测系统"],
        },
    )
    db_session.add(other_job)
    db_session.commit()
    db_session.refresh(other_job)
    second = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=other_job)

    job_rows = list_interview_preps(profile_id=None, job_id=job.id, limit=50, db=db_session)
    profile_rows = list_interview_preps(profile_id=profile.id, job_id=None, limit=50, db=db_session)

    assert [row.id for row in job_rows] == [first.id]
    assert {row.id for row in profile_rows} == {first.id, second.id}


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
