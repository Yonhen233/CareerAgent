import json

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMClient
from app.core.llm import LLMConfigurationError
from app.models.entities import Application, Job, Profile, ResumeVersion
from app.services.application_guardrails import ApplicationPacketGuardrail


class ApplicationService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMClient()
        self.guardrail = ApplicationPacketGuardrail()

    async def create_quick_apply_packet(
        self,
        db: Session,
        *,
        profile: Profile,
        job: Job,
        resume_version: ResumeVersion | None,
        browser_assist: bool = False,
    ) -> Application:
        cover_letter = await self._cover_letter(profile, job, resume_version)
        outreach = await self._outreach_message(profile, job)
        checklist = [
            "确认目标岗位和投递链接",
            "确认定制简历没有新增事实或无法证明的量化指标",
            "在招聘站登录后上传定制简历",
            "提交前人工确认隐私授权和必填字段",
        ]
        automation_result = {
            "browser_assist_requested": browser_assist,
            "mode": "manual_confirm_required",
            "final_submission": "user_confirmed_only",
            "message": "CareerAgent 只准备投递材料和目标链接；最终提交必须由用户人工确认。",
        }
        validation = self.guardrail.validate(
            profile=profile,
            job=job,
            resume_version=resume_version,
            cover_letter=cover_letter,
            outreach_message=outreach,
            checklist=checklist,
            automation_result=automation_result,
        )
        automation_result["packet_validation"] = validation
        automation_result["validation_passed"] = validation["passed"]
        if not validation["passed"]:
            issue_codes = ", ".join(issue["code"] for issue in validation["issues"])
            raise ValueError(f"Application packet guardrail failed: {issue_codes}")
        application = Application(
            profile_id=profile.id,
            job_id=job.id,
            resume_version_id=resume_version.id if resume_version else None,
            status="ready",
            apply_url=job.apply_url,
            cover_letter=cover_letter,
            outreach_message=outreach,
            checklist_json=checklist,
            automation_result_json=automation_result,
        )
        db.add(application)
        db.commit()
        db.refresh(application)
        return application

    async def _cover_letter(self, profile: Profile, job: Job, resume_version: ResumeVersion | None) -> str:
        fallback = self._fallback_cover_letter(profile, job, resume_version)
        if not self.llm.available:
            if not self.settings.llm_fallback_enabled:
                raise LLMConfigurationError(
                    "LLM is required for cover letter generation. Set LLM_FALLBACK_ENABLED=true for tests."
                )
            return fallback
        system_prompt = "You write concise Chinese job application letters. Return plain text only."
        user_prompt = f"""
Write a concise cover letter in Chinese.
Do not fabricate facts. Use the resume version if available.

Profile:
{json.dumps(profile.structured_profile_json, ensure_ascii=False)}

Job:
{job.title}
{job.company}
{job.raw_jd_text}

Resume version:
{resume_version.tailored_resume_markdown if resume_version else ""}
"""
        try:
            return await self.llm.generate_text(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.25)
        except Exception:
            if not self.settings.llm_fallback_enabled:
                raise
            return fallback

    async def _outreach_message(self, profile: Profile, job: Job) -> str:
        skills = self._profile_skills(profile)
        skill_text = "、".join(skills[:4]) if skills else "相关项目实践"
        target_role = (profile.target_roles_json or [job.title])[0] if profile.target_roles_json else job.title
        return (
            f"您好，我关注到 {job.company or '贵司'} 的 {job.title} 岗位。"
            f"我正在寻找 {target_role} 相关机会，已有 {skill_text} 等相关经历，"
            "希望有机会进一步交流。"
        )

    def _fallback_cover_letter(
        self,
        profile: Profile,
        job: Job,
        resume_version: ResumeVersion | None,
    ) -> str:
        skills = self._profile_skills(profile)
        projects = (profile.structured_profile_json or {}).get("projects") or []
        project = projects[0] if projects and isinstance(projects[0], dict) else {}
        project_name = project.get("name") or "相关项目"
        project_desc = project.get("description") or project.get("impact") or "积累了与岗位相关的工程实践"
        skill_text = "、".join(skills[:6]) if skills else "岗位相关技能"
        resume_note = "我已基于该岗位准备了定制简历，" if resume_version else ""
        return (
            f"您好，我是{profile.name or '候选人'}，希望申请 {job.company or '贵司'} 的 {job.title}。"
            f"{resume_note}我的相关经历包括 {skill_text}。"
            f"在 {project_name} 中，{project_desc}。"
            "如果岗位需要更多材料，我可以继续补充项目细节和可验证成果。期待进一步沟通。"
        )

    def _profile_skills(self, profile: Profile) -> list[str]:
        skills = (profile.structured_profile_json or {}).get("skills") or []
        return [str(skill).strip() for skill in skills if str(skill).strip()]
