import json

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMClient, llm_trace_context
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
        idempotency_key: str | None = None,
    ) -> Application:
        if idempotency_key:
            existing = db.query(Application).filter(Application.idempotency_key == idempotency_key).first()
            if existing is not None:
                return existing
        cover_letter = await self._cover_letter(db, profile, job, resume_version)
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
        repair_attempted = False
        if self._should_repair_cover_letter(validation) and self.llm.available:
            repaired_cover_letter = await self._repair_cover_letter(
                db,
                profile=profile,
                job=job,
                resume_version=resume_version,
                previous_draft=cover_letter,
                validation=validation,
            )
            if repaired_cover_letter:
                cover_letter = repaired_cover_letter
                repair_attempted = True
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
        automation_result["cover_letter_repair_attempted"] = repair_attempted
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
            idempotency_key=idempotency_key,
        )
        db.add(application)
        try:
            db.commit()
            db.refresh(application)
            return application
        except IntegrityError:
            db.rollback()
            if not idempotency_key:
                raise
            existing = db.query(Application).filter(Application.idempotency_key == idempotency_key).first()
            if existing is None:
                raise
            return existing

    async def _cover_letter(
        self,
        db: Session,
        profile: Profile,
        job: Job,
        resume_version: ResumeVersion | None,
    ) -> str:
        fallback = self._fallback_cover_letter(profile, job, resume_version)
        if not self.llm.available:
            if not self.settings.llm_fallback_enabled:
                raise LLMConfigurationError(
                    "LLM is required for cover letter generation. Set LLM_FALLBACK_ENABLED=true for tests."
                )
            return fallback
        system_prompt = (
            "You write concise Chinese job application letters. Return plain text only. "
            "Every candidate experience or achievement sentence must be a close paraphrase of the supplied profile. "
            "Do not add causal outcomes such as ensuring reliability or improving performance unless the source says so. "
            "Prefer copying the source wording for project and experience claims; if a claim cannot be supported by an exact source phrase, omit it. "
            "The first line must preserve the supplied company and job title exactly, without translation."
        )
        user_prompt = f"""
Write a concise cover letter in Chinese.
The first line must be exactly: 申请目标：{job.company or '未提供公司'} | {job.title}
Do not fabricate facts. Use the resume version if available.
Do not mention planned learning, missing skills, or unsupported JD requirements.
Every candidate claim must be a close paraphrase of the profile or resume version.
For project and experience sentences, copy the source wording or keep the same language and facts. Do not translate an outcome into a stronger claim.

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
            with llm_trace_context(
                workflow="application_packet",
                stage="cover_letter",
                profile_id=profile.id,
                job_id=job.id,
            ):
                generated = await self.llm.generate_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.25,
                    max_tokens=900,
                    db=db,
                    trace_name="application.cover_letter",
                )
                return self._ensure_job_target(generated, job)
        except Exception:
            if not self.settings.llm_fallback_enabled:
                raise
            return fallback

    def _should_repair_cover_letter(self, validation: dict) -> bool:
        issue_codes = {str(item.get("code") or "") for item in validation.get("issues") or []}
        return "unsupported_evidence_claims" in issue_codes and "unsupported_claims" not in issue_codes

    async def _repair_cover_letter(
        self,
        db: Session,
        *,
        profile: Profile,
        job: Job,
        resume_version: ResumeVersion | None,
        previous_draft: str,
        validation: dict,
    ) -> str | None:
        """Regenerate only evidence-weak prose, once, before packet rejection."""
        system_prompt = (
            "You repair a Chinese job application letter. Return plain text only. "
            "Keep the exact first line target. Remove any sentence that is not directly supported by the profile. "
            "Copy source phrases for project facts; never add metrics, outcomes, tools or experience."
        )
        issue_text = "\n".join(
            str(item.get("message") or item.get("code") or "")
            for item in validation.get("issues") or []
        )
        prompt = f"""
Repair this draft so it passes evidence validation.
Target first line (must be exact): 申请目标：{job.company or '未提供公司'} | {job.title}
Validation issue:
{issue_text}

Previous draft:
{previous_draft[:5000]}

Profile facts:
{json.dumps(profile.structured_profile_json or {}, ensure_ascii=False)[:9000]}

Original resume text:
{(profile.raw_resume_text or '')[:9000]}

Tailored resume, if any:
{(resume_version.tailored_resume_markdown if resume_version else '')[:9000]}

Use only the supplied facts. Prefer short sentences copied from the source. Omit unsupported claims instead of guessing.
"""
        try:
            with llm_trace_context(
                workflow="application_packet",
                stage="cover_letter_repair",
                profile_id=profile.id,
                job_id=job.id,
            ):
                generated = await self.llm.generate_text(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    temperature=0,
                    max_tokens=900,
                    db=db,
                    trace_name="application.cover_letter.repair",
                )
            return self._ensure_job_target(generated, job)
        except Exception:
            return None

    async def _outreach_message(self, profile: Profile, job: Job) -> str:
        skills = self._profile_skills(profile)
        skill_text = "、".join(skills[:4]) if skills else "相关项目实践"
        target_role = (profile.target_roles_json or [job.title])[0] if profile.target_roles_json else job.title
        return (
            f"您好，我关注到 {job.company or '贵司'} 的 {job.title} 岗位。"
            f"我正在寻找 {target_role} 相关机会。已有 {skill_text} 等相关经历，"
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
            f"申请目标：{job.company or '未提供公司'} | {job.title}\n"
            f"您好，我是{profile.name or '候选人'}，希望申请 {job.company or '贵司'} 的 {job.title}。"
            f"{resume_note}我的相关经历包括 {skill_text}。"
            f"在 {project_name} 中，{project_desc}。"
            "如果岗位需要更多材料，我可以继续补充项目细节和可验证成果。期待进一步沟通。"
        )

    def _ensure_job_target(self, text: str, job: Job) -> str:
        target = f"申请目标：{job.company or '未提供公司'} | {job.title}"
        clean = (text or "").strip()
        if clean.startswith(target):
            return clean
        return f"{target}\n{clean}".strip()

    def _profile_skills(self, profile: Profile) -> list[str]:
        skills = (profile.structured_profile_json or {}).get("skills") or []
        return [str(skill).strip() for skill in skills if str(skill).strip()]
