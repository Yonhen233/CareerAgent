import asyncio

import pytest

from app.models.entities import Job, Profile
from app.services.application_guardrails import ApplicationPacketGuardrail
from app.services.application_service import ApplicationService


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

    async def fabricated_cover_letter(profile, job, resume_version):
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
