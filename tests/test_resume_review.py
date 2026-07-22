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


def test_resume_review_rejects_llm_suggestions_with_unsupported_numbers(db_session):
    profile = _profile()
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    service = ResumeReviewService()

    class FakeLLM:
        available = True

        async def generate_json(self, **_kwargs):
            return {
                "strengths": ["已有 120 条评测样本，可继续突出评测方法。"],
                "suggestions": [
                    {
                        "priority": "high",
                        "section": "项目经历",
                        "suggestion_type": "rewrite_supported",
                        "source_quote": "构建 120 条噪声评测样本，统计 recall、false positive rate 和端到端耗时。",
                        "problem": "指标不够具体",
                        "advice": "改成在 500 份简历上达到 92% 召回率。",
                        "example_rewrite": "处理 500 份简历，Top-5 召回率达到 92%。",
                    },
                    {
                        "priority": "medium",
                        "section": "项目经历",
                        "suggestion_type": "rewrite_supported",
                        "source_quote": "构建 120 条噪声评测样本，统计 recall、false positive rate 和端到端耗时。",
                        "problem": "现有证据没有前置",
                        "advice": "把原简历已有的 120 条评测样本放到第一条。",
                        "example_rewrite": "构建 120 条噪声评测样本并统计召回率与误报率。",
                    },
                ],
            }

    service.llm = FakeLLM()
    review = asyncio.run(service.review_profile(db_session, profile=profile, include_llm=True))

    rendered = str(review["suggestions"])
    assert "500" not in rendered
    assert "92%" not in rendered
    assert "120 条噪声评测样本" in rendered
    assert review["trace"]["llm_rejected_suggestions"] == [
        {
            "reason": "unsupported_numeric_claim",
            "numbers": ["5", "500", "92"],
            "section": "项目经历",
        }
    ]


def test_resume_review_rejects_ungrounded_llm_rewrite_without_numeric_claims(db_session):
    profile = _profile()
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    service = ResumeReviewService()

    class FakeLLM:
        available = True

        async def generate_json(self, **_kwargs):
            return {
                "strengths": [],
                "suggestions": [
                    {
                        "priority": "high",
                        "section": "项目经历",
                        "suggestion_type": "rewrite_supported",
                        "source_quote": "CareerAgent 项目实现 PDF Chunk、SQLite RAG、LangGraph workflow 和 LLM trace。",
                        "problem": "没有覆盖岗位职责",
                        "advice": "补充工具调用精度和错误恢复率。",
                        "example_rewrite": "构建 Agent 评估管线，评估工具参数准确性和错误恢复能力。",
                    }
                ],
            }

    service.llm = FakeLLM()
    review = asyncio.run(service.review_profile(db_session, profile=profile, include_llm=True))

    assert "工具参数准确性" not in str(review["suggestions"])
    assert review["trace"]["llm_rejected_suggestions"][0]["reason"] == "insufficient_evidence_overlap"


def test_deterministic_review_examples_only_use_profile_skills(db_session):
    profile = _profile(
        target_roles_json=["Agent 开发实习生"],
        structured_profile_json={
            "name": "李明",
            "target_roles": ["Agent 开发实习生"],
            "skills": ["Python", "FastAPI", "RAG"],
            "projects": [{"name": "CareerAgent", "description": "实现岗位检索与简历匹配。"}],
            "education": [],
        },
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    review = asyncio.run(ResumeReviewService().review_profile(db_session, profile=profile, include_llm=False))
    rendered = str(review["suggestions"])

    assert "Python" in rendered
    assert "FastAPI" in rendered
    assert "LangGraph" not in rendered
    assert "待补充真实数据" in rendered
