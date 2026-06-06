from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AgentSkillSpec:
    name: str
    status: str
    owner_subagent: str
    purpose: str
    trigger: str
    tools: list[str]
    context_policy: str
    output_contract: dict[str, str]
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


AGENT_SKILLS: list[AgentSkillSpec] = [
    AgentSkillSpec(
        name="resume_intake_and_structuring",
        status="active",
        owner_subagent="profile_analyst",
        purpose="把 PDF/文本简历转成可检索、可审计的候选人 Profile。",
        trigger="用户上传 PDF 或粘贴简历文本时。",
        tools=["resume_parser.parse_structured_resume", "vector_index.upsert_profile_chunks"],
        context_policy="允许完整简历输入；解析后只向下游传递结构化 Profile 和压缩摘要。",
        output_contract={"profile_json": "dict", "resume_chunks": "list"},
    ),
    AgentSkillSpec(
        name="jd_structuring",
        status="active",
        owner_subagent="job_analyst",
        purpose="把真实 JD 解析成 required skills、职责和资格条件。",
        trigger="新增岗位或岗位搜索入库时。",
        tools=["jd_parser.parse_jd", "vector_index.upsert_job_chunks"],
        context_policy="允许完整 JD 输入；向匹配和定制阶段传递结构化 JD 与压缩 JD 摘要。",
        output_contract={"structured_jd": "dict", "job_chunks": "list"},
    ),
    AgentSkillSpec(
        name="evidence_retrieval",
        status="active",
        owner_subagent="evidence_curator",
        purpose="检索简历和 JD 的可解释证据，并过滤 planned/coursework 类弱证据。",
        trigger="岗位匹配、fit judge、简历定制前。",
        tools=["vector_index.retrieve_resume_evidence", "matcher.match_job"],
        context_policy="只保留 Top evidence、retrieval score、rerank metadata 和必要文本片段。",
        output_contract={"evidence": "list[RetrievedChunk]", "match_result": "dict"},
    ),
    AgentSkillSpec(
        name="progressive_disclosure",
        status="active",
        owner_subagent="context_manager",
        purpose="按任务阶段逐步披露 Profile、JD 和 evidence，避免一次性把全量上下文塞进 LLM。",
        trigger="fit judge、简历定制、投递包生成或 Guardrail repair 前。",
        tools=["context_compressor.compress_fit_context", "context_compressor.compress_tailor_context"],
        context_policy="默认只暴露结构化摘要和 Top evidence；只有修复循环需要具体引用时才请求更细粒度原文。",
        output_contract={"compressed_context": "dict", "context_compression": "dict"},
    ),
    AgentSkillSpec(
        name="fit_assessment",
        status="active",
        owner_subagent="fit_judge",
        purpose="基于压缩上下文判断 strong/partial/weak fit，并给出证据和缺口。",
        trigger="用户比较岗位或运行 LLM workflow 评测时。",
        tools=["llm.generate_json", "context_compressor.compress_fit_context"],
        context_policy="必须使用压缩上下文；禁止读取未压缩全量历史。",
        output_contract={"fit_label": "str", "fit_score": "number", "matched_evidence": "list", "gaps": "list"},
    ),
    AgentSkillSpec(
        name="resume_tailoring",
        status="active",
        owner_subagent="resume_writer",
        purpose="根据 JD 与证据生成定制简历，并保持事实可追溯。",
        trigger="用户要求针对某岗位改简历时。",
        tools=["context_compressor.compress_tailor_context", "resume_tailor.tailor_resume", "guardrail.verify_resume"],
        context_policy="必须使用压缩上下文和 Top evidence；Guardrail 失败时进入修复或报错。",
        output_contract={"resume_markdown": "str", "change_summary": "list", "keyword_alignment": "dict"},
    ),
    AgentSkillSpec(
        name="application_packet",
        status="active",
        owner_subagent="application_operator",
        purpose="生成投递包、求职信、外联消息和人工确认清单。",
        trigger="用户准备投递时。",
        tools=["application.create_quick_apply_packet", "llm.generate_text"],
        context_policy="只读取 Profile 摘要、目标 JD 摘要和最终定制简历。",
        output_contract={"application": "dict", "checklist": "list"},
    ),
]


def list_agent_skills(*, include_deferred: bool = True) -> list[dict[str, Any]]:
    skills = AGENT_SKILLS if include_deferred else [skill for skill in AGENT_SKILLS if skill.status == "active"]
    return [skill.as_dict() for skill in skills]


def active_skill_names_for_task(task_type: str) -> list[str]:
    mapping = {
        "find_jobs_for_profile": ["resume_intake_and_structuring", "jd_structuring", "evidence_retrieval"],
        "tailor_resume_for_job": [
            "evidence_retrieval",
            "progressive_disclosure",
            "fit_assessment",
            "resume_tailoring",
        ],
        "quick_apply": ["progressive_disclosure", "resume_tailoring", "application_packet"],
    }
    return mapping.get(task_type, [])
