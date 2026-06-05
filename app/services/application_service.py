import json

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMClient
from app.core.llm import LLMConfigurationError
from app.models.entities import Application, Job, Profile, ResumeVersion


class ApplicationService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMClient()

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
            "message": "CareerAgent prepares the application packet and target URL; final submission stays user-confirmed.",
        }
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
        fallback = (
            f"您好，我是{profile.name or '候选人'}，希望申请 {job.company or ''} {job.title}。"
            "我的项目经历集中在 Agent 工作流、RAG 检索、FastAPI 服务化和 SQLite 数据持久化，"
            "可以较快参与真实业务中的 AI 应用开发、评测与工程落地。期待进一步沟通。"
        )
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
        return (
            f"您好，我关注到 {job.company or '贵司'} 的 {job.title} 岗位。"
            f"我正在寻找 Agent 开发相关实习，已有 {', '.join((profile.structured_profile_json or {}).get('skills', [])[:6])} "
            "等相关经验，希望有机会交流。"
        )
