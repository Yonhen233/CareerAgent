from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SubAgentSpec:
    name: str
    purpose: str
    owns_skills: list[str]
    reads: list[str]
    writes: list[str]
    context_policy: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


SUBAGENTS: list[SubAgentSpec] = [
    SubAgentSpec(
        name="profile_analyst",
        purpose="解析候选人 Profile，识别真实项目、技能和弱证据。",
        owns_skills=["resume_intake_and_structuring"],
        reads=["resume_raw_text", "guided_profile"],
        writes=["structured_profile", "resume_chunks"],
        context_policy="可读取完整简历；下游只接收结构化结果和压缩摘要。",
    ),
    SubAgentSpec(
        name="job_analyst",
        purpose="解析 JD，提取岗位硬要求、软要求和职责。",
        owns_skills=["jd_structuring"],
        reads=["raw_jd_text", "job_source_payload"],
        writes=["structured_jd", "job_chunks"],
        context_policy="可读取完整 JD；下游只接收结构化 JD 和压缩摘要。",
    ),
    SubAgentSpec(
        name="evidence_curator",
        purpose="检索和整理证据，区分真实交付、课程、计划学习和相邻项目。",
        owns_skills=["evidence_retrieval"],
        reads=["structured_profile", "structured_jd", "resume_chunks", "job_chunks"],
        writes=["ranked_evidence", "match_result"],
        context_policy="保留 Top evidence 和排序元数据，压缩后再交给 LLM。",
    ),
    SubAgentSpec(
        name="fit_judge",
        purpose="使用压缩上下文判断岗位适配度，并输出证据与缺口。",
        owns_skills=["fit_assessment"],
        reads=["compressed_fit_context"],
        writes=["fit_label", "fit_score", "matched_evidence", "gaps"],
        context_policy="禁止读取全量历史，只使用 ContextCompressor 输出。",
    ),
    SubAgentSpec(
        name="resume_writer",
        purpose="生成定制简历并接受 Guardrail 验证。",
        owns_skills=["resume_tailoring"],
        reads=["compressed_tailor_context", "guardrail_result"],
        writes=["resume_version", "change_summary", "keyword_alignment"],
        context_policy="只使用压缩上下文和已检索证据，避免长上下文漂移。",
    ),
    SubAgentSpec(
        name="application_operator",
        purpose="准备投递包和人工确认清单。",
        owns_skills=["application_packet"],
        reads=["profile_summary", "job_summary", "resume_version"],
        writes=["application_packet", "cover_letter", "outreach_message"],
        context_policy="只读取最终简历版本和岗位摘要。",
    ),
    SubAgentSpec(
        name="interview_coach",
        purpose="把岗位要求、候选人证据和能力缺口转成面试准备问题与诚实回答策略。",
        owns_skills=["interview_preparation"],
        reads=["structured_jd", "match_result", "ranked_evidence"],
        writes=["interview_prep", "question_sets", "gap_drills", "research_checklist", "interview_experience_evidence"],
        context_policy="只读取 Top evidence 和缺口摘要；缺少证据时输出披露策略，不生成虚假经历。",
    ),
]


TASK_SUBAGENTS = {
    "find_jobs_for_profile": ["profile_analyst", "job_analyst", "evidence_curator"],
    "tailor_resume_for_job": [
        "profile_analyst",
        "job_analyst",
        "evidence_curator",
        "fit_judge",
        "resume_writer",
    ],
    "quick_apply": ["profile_analyst", "job_analyst", "resume_writer", "application_operator"],
    "prepare_interview_for_job": ["profile_analyst", "job_analyst", "evidence_curator", "fit_judge", "interview_coach"],
    "full_career_flow": [
        "profile_analyst",
        "job_analyst",
        "evidence_curator",
        "fit_judge",
        "resume_writer",
        "application_operator",
        "interview_coach",
    ],
}


def list_subagents() -> list[dict[str, Any]]:
    return [subagent.as_dict() for subagent in SUBAGENTS]


def subagents_for_task(task_type: str) -> list[dict[str, Any]]:
    wanted = set(TASK_SUBAGENTS.get(task_type, []))
    return [subagent.as_dict() for subagent in SUBAGENTS if subagent.name in wanted]
