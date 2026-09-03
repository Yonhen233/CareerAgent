import asyncio

import pytest

from app.models.entities import Job, Profile
from app.services.embedding_service import EmbeddingBatch
from app.services.application_guardrails import ApplicationPacketGuardrail
from app.services.evidence_grounding import EvidenceGroundingService
from app.services.application_service import ApplicationService
from app.services.evidence_grounding import EvidenceGroundingService


def _manual_automation() -> dict:
    return {
        "mode": "manual_confirm_required",
        "final_submission": "user_confirmed_only",
    }


def _checklist() -> list[str]:
    return ["确认目标岗位和投递链接", "提交前人工确认隐私授权和必填字段"]


def test_application_guardrail_rejects_unsupported_skill_claims():
    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Built PyTorch evaluation dashboards. No MLflow experience.",
        structured_profile_json={
            "skills": ["Python", "PyTorch", "Evaluation"],
            "projects": [{"name": "VisionBench", "description": "Built PyTorch evaluation dashboards."}],
            "raw_text": "Built PyTorch evaluation dashboards. No MLflow experience.",
        },
    )
    job = Job(
        source="manual",
        external_id="ml-platform",
        title="机器学习平台实习生",
        company="MLWorks",
        raw_jd_text="维护 MLflow、Kubernetes 和模型评测平台。",
        structured_jd_json={"required_skills": ["MLflow", "Kubernetes", "PyTorch"]},
        apply_url="https://example.com/jobs/ml-platform",
    )

    validation = ApplicationPacketGuardrail().validate(
        profile=profile,
        job=job,
        resume_version=None,
        cover_letter="您好，我希望申请 MLWorks 的机器学习平台实习生。我熟悉 MLflow 和 Kubernetes，并有平台落地经验。",
        outreach_message="您好，我关注到 MLWorks 的机器学习平台实习生岗位，希望交流。",
        checklist=_checklist(),
        automation_result=_manual_automation(),
    )

    assert validation["passed"] is False
    assert validation["risk_level"] == "high"
    issue = validation["issues"][0]
    assert issue["code"] == "unsupported_claims"
    assert {"MLflow", "Kubernetes"} <= set(issue["terms"])


def test_application_service_dynamic_fallback_matches_non_agent_role(db_session):
    profile = Profile(
        name="Zhao Lin",
        email="zhaolin@example.com",
        headline="前端开发实习生候选人",
        target_roles_json=["前端开发实习生"],
        source_type="guided",
        raw_resume_text="Built React components with TypeScript, CSS, Playwright and accessibility checks.",
        structured_profile_json={
            "name": "Zhao Lin",
            "skills": ["React", "TypeScript", "CSS", "Playwright", "Accessibility"],
            "projects": [
                {
                    "name": "DashboardStudio",
                    "description": "Built React components, TypeScript hooks and Playwright visual tests.",
                    "tech_stack": ["React", "TypeScript", "CSS", "Playwright"],
                }
            ],
            "raw_text": "Built React components with TypeScript, CSS, Playwright and accessibility checks.",
        },
    )
    job = Job(
        source="manual",
        external_id="frontend-intern",
        title="前端开发实习生",
        company="DesignOps",
        raw_jd_text="负责 React、TypeScript、CSS 和可访问性建设。",
        structured_jd_json={"required_skills": ["React", "TypeScript", "CSS", "Accessibility"]},
        apply_url="https://example.com/jobs/frontend-intern",
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)

    application = asyncio.run(
        ApplicationService().create_quick_apply_packet(
            db_session,
            profile=profile,
            job=job,
            resume_version=None,
        )
    )

    assert application.status == "ready"
    assert "前端开发实习生" in application.cover_letter
    assert "React" in application.cover_letter
    assert "Agent 工作流" not in application.cover_letter
    validation = application.automation_result_json["packet_validation"]
    assert validation["passed"] is True
    assert application.automation_result_json["final_submission"] == "user_confirmed_only"
    assert application.automation_result_json["validation_passed"] is True


def test_application_service_fails_when_llm_fabricates_claims(db_session, monkeypatch):
    profile = Profile(
        name="Ma Chen",
        source_type="guided",
        raw_resume_text="Built PyTorch evaluation dashboards. No MLflow experience.",
        structured_profile_json={
            "skills": ["Python", "PyTorch", "Evaluation"],
            "projects": [{"name": "VisionBench", "description": "Built PyTorch evaluation dashboards."}],
            "raw_text": "Built PyTorch evaluation dashboards. No MLflow experience.",
        },
    )
    job = Job(
        source="manual",
        external_id="ml-platform-2",
        title="机器学习平台实习生",
        company="MLWorks",
        raw_jd_text="维护 MLflow、Kubernetes 和模型评测平台。",
        structured_jd_json={"required_skills": ["MLflow", "Kubernetes", "PyTorch"]},
        apply_url="https://example.com/jobs/ml-platform-2",
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)
    service = ApplicationService()

    async def fabricated_cover_letter(db, profile, job, resume_version):
        return "您好，我希望申请 MLWorks 的机器学习平台实习生。我熟悉 MLflow 和 Kubernetes，并有平台落地经验。"

    monkeypatch.setattr(service, "_cover_letter", fabricated_cover_letter)

    with pytest.raises(ValueError, match="Application packet guardrail failed: unsupported_claims"):
        asyncio.run(
            service.create_quick_apply_packet(
                db_session,
                profile=profile,
                job=job,
                resume_version=None,
            )
        )


def test_application_guardrail_rejects_unknown_semantic_achievement():
    profile = Profile(
        name="Li Ming",
        source_type="guided",
        raw_resume_text="使用 Python 和 FastAPI 构建 CareerAgent。",
        structured_profile_json={"skills": ["Python", "FastAPI"]},
    )
    job = Job(
        source="manual",
        external_id="agent-intern",
        title="Agent 开发实习生",
        company="Example AI",
        raw_jd_text="负责 Agent 平台开发。",
        structured_jd_json={"required_skills": ["Python", "FastAPI"]},
        apply_url="https://example.com/apply",
    )

    validation = ApplicationPacketGuardrail().validate(
        profile=profile,
        job=job,
        resume_version=None,
        cover_letter=(
            "您好，我希望申请 Example AI 的 Agent 开发实习生。"
            "我主导跨地域容灾系统并完成生产切换，希望将相关经验用于该岗位。"
        ),
        outreach_message="您好，我关注到 Example AI 的 Agent 开发实习生岗位，希望交流。",
        checklist=_checklist(),
        automation_result=_manual_automation(),
    )

    assert validation["passed"] is False
    assert "unsupported_evidence_claims" in {item["code"] for item in validation["issues"]}


def test_application_guardrail_accepts_grounded_cross_language_project_paraphrase():
    class CrossLanguageEmbeddingStub:
        def embed_texts(self, texts):
            return EmbeddingBatch(
                vectors=[[1.0, 0.0] for _ in texts],
                provider="cross_language_test",
                model="controlled",
                dimensions=2,
            )

    profile = Profile(
        name="Xu Yan",
        source_type="guided",
        raw_resume_text=(
            "Project AgentOps: implemented LangGraph event streaming, structured logs, health probes "
            "and graceful worker drain. Did not implement distributed tracing across external providers."
        ),
        structured_profile_json={"skills": ["Python", "LangGraph", "OpenTelemetry"]},
    )
    job = Job(
        source="manual",
        external_id="agent-observability",
        title="Agent Observability Intern",
        company="FlowAI",
        raw_jd_text="Build Agent observability tooling.",
        structured_jd_json={"required_skills": ["LangGraph", "OpenTelemetry"]},
        apply_url="https://example.com/apply",
    )

    validation = ApplicationPacketGuardrail(
        embedding_service=CrossLanguageEmbeddingStub()
    ).validate(
        profile=profile,
        job=job,
        resume_version=None,
        cover_letter=(
            "尊敬的 FlowAI 招聘团队，我申请 Agent Observability Intern。"
            "在 AgentOps 项目中，我实现了 LangGraph 事件流以支持运行时可观测性，"
            "并开发了健康检查探针与优雅的工作节点排空机制。"
        ),
        outreach_message="您好，我关注到 FlowAI 的 Agent Observability Intern，希望进一步交流。",
        checklist=_checklist(),
        automation_result=_manual_automation(),
    )

    assert validation["passed"] is True
    semantic = validation["semantic_claim_grounding"]
    assert semantic["embedding"]["provider"] == "cross_language_test"
    assert semantic["results"][0]["support_method"] == "multilingual_embedding"


def test_application_guardrail_does_not_treat_accessibility_as_chinese_negation():
    class CrossLanguageEmbeddingStub:
        def embed_texts(self, texts):
            return EmbeddingBatch(
                vectors=[[1.0, 0.0] for _ in texts],
                provider="cross_language_test",
                model="controlled",
                dimensions=2,
            )

    profile = Profile(
        name="Zhao Lin",
        source_type="guided",
        raw_resume_text="Built Playwright visual tests and accessibility checks.",
        structured_profile_json={"skills": ["Playwright", "Accessibility"]},
    )
    job = Job(
        source="manual",
        external_id="frontend-accessibility",
        title="Frontend Engineering Intern",
        company="DesignOps",
        raw_jd_text="Build accessible frontend components.",
        structured_jd_json={"required_skills": ["Playwright", "Accessibility"]},
        apply_url="https://example.com/apply",
    )

    validation = ApplicationPacketGuardrail(
        embedding_service=CrossLanguageEmbeddingStub()
    ).validate(
        profile=profile,
        job=job,
        resume_version=None,
        cover_letter=(
            "我申请 DesignOps 的 Frontend Engineering Intern。"
            "我实现了 Playwright 视觉测试和无障碍检查。"
        ),
        outreach_message="您好，我关注 DesignOps 的 Frontend Engineering Intern，希望交流。",
        checklist=_checklist(),
        automation_result=_manual_automation(),
    )

    assert validation["passed"] is True
    assert ApplicationPacketGuardrail()._is_negative_claim("我实现了无障碍检查") is False


def test_application_service_preserves_exact_english_job_target():
    job = Job(title="Frontend Engineering Intern", company="DesignOps")
    service = ApplicationService()

    output = service._ensure_job_target("尊敬的招聘团队：\n我申请前端开发实习生。", job)

    assert output.startswith("申请目标：DesignOps | Frontend Engineering Intern\n")


def test_application_guardrail_recovers_borderline_translation_only_with_supported_taxonomy():
    import math

    class BorderlineEmbeddingStub:
        def embed_texts(self, texts):
            vectors = [[1.0, 0.0]]
            vectors.extend([[0.68, math.sqrt(1 - 0.68**2)] for _ in texts[1:]])
            return EmbeddingBatch(
                vectors=vectors,
                provider="borderline_translation_test",
                model="controlled",
                dimensions=2,
            )

    guardrail = ApplicationPacketGuardrail(embedding_service=BorderlineEmbeddingStub())
    report = guardrail._recover_multilingual_grounding(
        {
            "passed": False,
            "results": [{"claim": "我实现了无障碍检查", "support_score": 0.2, "supported": False}],
            "unsupported_claims": [
                {"claim": "我实现了无障碍检查", "support_score": 0.2, "supported": False}
            ],
        },
        support_sources=["Built accessibility checks."],
        grounding=EvidenceGroundingService(),
        supported_terms={"Accessibility"},
    )

    assert report["passed"] is True
    assert report["results"][0]["support_method"] == "multilingual_embedding_taxonomy_corroborated"


def test_application_guardrail_does_not_recover_borderline_unsupported_taxonomy():
    import math

    class BorderlineEmbeddingStub:
        def embed_texts(self, texts):
            vectors = [[1.0, 0.0]]
            vectors.extend([[0.68, math.sqrt(1 - 0.68**2)] for _ in texts[1:]])
            return EmbeddingBatch(
                vectors=vectors,
                provider="borderline_translation_test",
                model="controlled",
                dimensions=2,
            )

    guardrail = ApplicationPacketGuardrail(embedding_service=BorderlineEmbeddingStub())
    report = guardrail._recover_multilingual_grounding(
        {
            "passed": False,
            "results": [{"claim": "我部署了 Kubernetes", "support_score": 0.2, "supported": False}],
            "unsupported_claims": [
                {"claim": "我部署了 Kubernetes", "support_score": 0.2, "supported": False}
            ],
        },
        support_sources=["Built accessibility checks."],
        grounding=EvidenceGroundingService(),
        supported_terms={"Accessibility"},
    )

    assert report["passed"] is False


def test_application_guardrail_recovers_borderline_translated_structured_outcome():
    import math

    class BorderlineEmbeddingStub:
        def embed_texts(self, texts):
            vectors = [[1.0, 0.0]]
            vectors.extend([[0.68, math.sqrt(1 - 0.68**2)] for _ in texts[1:]])
            return EmbeddingBatch(
                vectors=vectors,
                provider="borderline_translation_test",
                model="controlled",
                dimensions=2,
            )

    guardrail = ApplicationPacketGuardrail(embedding_service=BorderlineEmbeddingStub())
    report = guardrail._recover_multilingual_grounding(
        {
            "passed": False,
            "results": [
                {
                    "claim": "该项目提升了组件复用率和 UI 回归覆盖率",
                    "support_score": 0.2,
                    "supported": False,
                }
            ],
            "unsupported_claims": [
                {
                    "claim": "该项目提升了组件复用率和 UI 回归覆盖率",
                    "support_score": 0.2,
                    "supported": False,
                }
            ],
        },
        support_sources=["Improved component reuse and UI regression coverage."],
        grounding=EvidenceGroundingService(),
        supported_terms=set(),
        trusted_fact_sources=["Improved component reuse and UI regression coverage."],
    )

    assert report["passed"] is True
    assert report["results"][0]["support_method"] == (
        "multilingual_embedding_structured_fact_corroborated"
    )


def test_application_guardrail_does_not_recover_changed_outcome_from_structured_fact():
    import math

    class BorderlineEmbeddingStub:
        def embed_texts(self, texts):
            vectors = [[1.0, 0.0]]
            vectors.extend([[0.68, math.sqrt(1 - 0.68**2)] for _ in texts[1:]])
            return EmbeddingBatch(
                vectors=vectors,
                provider="borderline_translation_test",
                model="controlled",
                dimensions=2,
            )

    guardrail = ApplicationPacketGuardrail(embedding_service=BorderlineEmbeddingStub())
    report = guardrail._recover_multilingual_grounding(
        {
            "passed": False,
            "results": [
                {"claim": "该项目提升了系统可靠性", "support_score": 0.2, "supported": False}
            ],
            "unsupported_claims": [
                {"claim": "该项目提升了系统可靠性", "support_score": 0.2, "supported": False}
            ],
        },
        support_sources=["Improved component reuse and UI regression coverage."],
        grounding=EvidenceGroundingService(),
        supported_terms=set(),
        trusted_fact_sources=["Improved component reuse and UI regression coverage."],
    )

    assert report["passed"] is False


def test_application_guardrail_embedding_does_not_reverse_negative_evidence():
    class SimilarityStub:
        def embed_texts(self, texts):
            return EmbeddingBatch(
                vectors=[[1.0, 0.0] for _ in texts],
                provider="similarity_test",
                model="controlled",
                dimensions=2,
            )

    profile = Profile(
        name="Xu Yan",
        source_type="guided",
        raw_resume_text="Did not implement distributed tracing across external model providers.",
        structured_profile_json={"skills": ["Python"]},
    )
    job = Job(
        source="manual",
        external_id="negative-evidence",
        title="Agent Observability Intern",
        company="FlowAI",
        raw_jd_text="Build distributed tracing.",
        structured_jd_json={"required_skills": ["distributed tracing"]},
        apply_url="https://example.com/apply",
    )

    validation = ApplicationPacketGuardrail(embedding_service=SimilarityStub()).validate(
        profile=profile,
        job=job,
        resume_version=None,
        cover_letter=(
            "尊敬的 FlowAI 招聘团队，我申请 Agent Observability Intern。"
            "我已经实现跨外部模型提供商的分布式追踪。"
        ),
        outreach_message="您好，我关注到 FlowAI 的 Agent Observability Intern，希望交流。",
        checklist=_checklist(),
        automation_result=_manual_automation(),
    )

    assert validation["passed"] is False
    semantic = validation["semantic_claim_grounding"]
    assert semantic["results"][0]["embedding_support_score"] == 1.0
    assert semantic["results"][0]["embedding_polarity_consistent"] is False


def test_application_guardrail_embedding_does_not_invent_outcome_from_related_implementation():
    class SimilarityStub:
        def embed_texts(self, texts):
            return EmbeddingBatch(
                vectors=[[1.0, 0.0] for _ in texts],
                provider="similarity_test",
                model="controlled",
                dimensions=2,
            )

    profile = Profile(
        name="Xu Yan",
        source_type="guided",
        raw_resume_text="Implemented health probes and graceful worker drain for Agent workers.",
        structured_profile_json={"skills": ["Python"]},
    )
    job = Job(
        source="manual",
        external_id="unsupported-outcome",
        title="Agent Platform Intern",
        company="FlowAI",
        raw_jd_text="Build Agent platform tooling.",
        structured_jd_json={"required_skills": ["Python"]},
        apply_url="https://example.com/apply",
    )

    validation = ApplicationPacketGuardrail(embedding_service=SimilarityStub()).validate(
        profile=profile,
        job=job,
        resume_version=None,
        cover_letter=(
            "尊敬的 FlowAI 招聘团队，我申请 Agent Platform Intern。"
            "我实现了健康探针和工作节点优雅退出，并确保平台可靠性提升。"
        ),
        outreach_message="您好，我关注到 FlowAI 的 Agent Platform Intern，希望交流。",
        checklist=_checklist(),
        automation_result=_manual_automation(),
    )

    assert validation["passed"] is False
    semantic = validation["semantic_claim_grounding"]
    assert semantic["results"][0]["embedding_support_score"] == 1.0
    assert semantic["results"][0]["embedding_outcome_semantics_consistent"] is False


def test_application_guardrail_does_not_treat_target_role_as_candidate_skill():
    profile = Profile(
        name="Chen Hang",
        source_type="guided",
        raw_resume_text="使用 Python 和 FastAPI 完成课程版知识库问答。",
        structured_profile_json={"skills": ["Python", "FastAPI"]},
    )
    job = Job(
        source="manual",
        external_id="agent-target-only",
        title="Agent 开发实习生",
        company="Example AI",
        raw_jd_text="负责 Agent 平台开发。",
        structured_jd_json={"required_skills": ["Agent", "Python"]},
        apply_url="https://example.com/apply",
    )

    validation = ApplicationPacketGuardrail().validate(
        profile=profile,
        job=job,
        resume_version=None,
        cover_letter=(
            "您好，我希望申请 Example AI 的 Agent 开发实习生。"
            "我使用 Python 和 FastAPI 完成课程版知识库问答。"
        ),
        outreach_message=(
            "您好，我关注到 Example AI 的 Agent 开发实习生岗位。"
            "已有 Python 和 FastAPI 相关经历，希望交流。"
        ),
        checklist=_checklist(),
        automation_result=_manual_automation(),
    )

    assert validation["passed"] is True


def test_grounding_number_matching_handles_decimal_adjacent_to_chinese_text():
    service = EvidenceGroundingService()

    assert service.unsupported_numbers("Recall@10为0.91。", ["Recall@10 为 0.91。"]) == []


def test_grounding_number_matching_accepts_decimal_before_english_period():
    service = EvidenceGroundingService()

    assert service.unsupported_numbers(
        "在评测中 Recall@10 达到 0.89。",
        ["Evaluated 320 queries and achieved Recall@10 of 0.89."],
    ) == []


def test_grounding_excludes_future_intent_from_candidate_claims():
    service = EvidenceGroundingService()

    claims = service.extract_candidate_claims(
        "期待有机会参与贵司研究助手的开发工作。"
        "我实现了 8 个只读工具。"
    )

    assert "期待有机会参与贵司研究助手的开发工作" not in claims
    assert "我实现了 8 个只读工具" in claims
