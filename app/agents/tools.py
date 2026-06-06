from dataclasses import asdict, dataclass
from typing import Any

from app.agents.skills import active_skill_names_for_task
from app.agents.subagents import subagents_for_task


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    purpose: str
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    side_effects: list[str]
    mcp_candidate: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


AGENT_TOOLS: list[AgentToolSpec] = [
    AgentToolSpec(
        name="profile_repository.load_profile",
        purpose="加载候选人档案和结构化简历。",
        input_schema={"profile_id": "int"},
        output_schema={"profile": "Profile"},
        side_effects=[],
    ),
    AgentToolSpec(
        name="job_search.search_jobs",
        purpose="并发搜索真实招聘源并返回岗位列表。",
        input_schema={"query": "str", "location": "str|None", "limit": "int"},
        output_schema={"jobs": "list[Job]", "source_errors": "dict"},
        side_effects=["external_http_requests", "optional_job_storage"],
        mcp_candidate=True,
    ),
    AgentToolSpec(
        name="job_repository.load_job",
        purpose="加载目标岗位、原始 JD 和结构化 JD。",
        input_schema={"job_id": "int"},
        output_schema={"job": "Job"},
        side_effects=[],
    ),
    AgentToolSpec(
        name="jd_parser.parse_jd",
        purpose="把原始 JD 解析成 required_skills、responsibilities、qualifications 等结构化字段。",
        input_schema={"raw_jd_text": "str", "title": "str|None", "company": "str|None"},
        output_schema={"structured_jd": "dict"},
        side_effects=["optional_llm_call", "llm_call_log"],
    ),
    AgentToolSpec(
        name="vector_index.upsert_job_chunks",
        purpose="切分并存储职位 JD chunk，同时写入 embedding 和可选 Chroma 镜像。",
        input_schema={"job_id": "int", "chunks": "list[TextChunk]"},
        output_schema={"inserted": "int"},
        side_effects=["sqlite_write", "optional_chroma_write", "embedding_model_call"],
    ),
    AgentToolSpec(
        name="matcher.match_job",
        purpose="计算岗位匹配分数、技能覆盖、缺口和可解释证据。",
        input_schema={"profile_id": "int", "job_id": "int"},
        output_schema={"match_result": "MatchResult"},
        side_effects=["sqlite_write", "embedding_model_call", "reranker_call"],
    ),
    AgentToolSpec(
        name="vector_index.retrieve_resume_evidence",
        purpose="基于 JD 查询简历 chunk，一阶段 Top20 检索后用 reranker 二阶段排序。",
        input_schema={"profile_id": "int", "query": "str", "top_k": "int"},
        output_schema={"evidence_chunks": "list[RetrievedChunk]"},
        side_effects=["embedding_model_call", "reranker_call"],
    ),
    AgentToolSpec(
        name="resume_tailor.tailor_resume",
        purpose="根据 JD 和 RAG 证据生成定制简历。",
        input_schema={"profile": "Profile", "job": "Job", "evidence": "list[dict]"},
        output_schema={"resume_version": "ResumeVersion"},
        side_effects=["optional_llm_call", "sqlite_write", "llm_call_log"],
    ),
    AgentToolSpec(
        name="guardrail.verify_resume",
        purpose="检查定制简历是否新增未经证据支持的事实，并计算关键词覆盖率。",
        input_schema={"profile": "Profile", "job": "Job", "resume_markdown": "str"},
        output_schema={"verification": "dict"},
        side_effects=[],
    ),
    AgentToolSpec(
        name="application.create_quick_apply_packet",
        purpose="生成投递包、求职信、外联文案、清单和投递链接。",
        input_schema={"profile": "Profile", "job": "Job", "resume_version": "ResumeVersion"},
        output_schema={"application": "Application"},
        side_effects=["sqlite_write", "optional_llm_call", "llm_call_log"],
        mcp_candidate=True,
    ),
]


def list_agent_tools() -> list[dict[str, Any]]:
    return [tool.as_dict() for tool in AGENT_TOOLS]


class AgentPlanner:
    def build_plan(self, request: Any) -> dict[str, Any]:
        task_type = str(getattr(request, "task_type", ""))
        if task_type == "find_jobs_for_profile":
            steps = [
                self._step("load_profile", "profile_repository.load_profile", "读取候选人目标岗位和技能画像。"),
                self._step("search_jobs", "job_search.search_jobs", "并发搜索招聘源，保留 source error。"),
                self._step("match_job", "matcher.match_job", "逐个岗位匹配并按 overall_score 排序。"),
            ]
            react_loops: list[dict[str, Any]] = []
        elif task_type == "tailor_resume_for_job":
            steps = [
                self._step("load_profile", "profile_repository.load_profile", "读取候选人原始事实。"),
                self._step("load_job", "job_repository.load_job", "读取目标 JD 和结构化字段。"),
                self._step("match_job", "matcher.match_job", "建立岗位匹配和缺口分析。"),
                self._step(
                    "retrieve_resume_evidence",
                    "vector_index.retrieve_resume_evidence",
                    "检索 Top20 简历证据并用 reranker 排序。",
                ),
                self._step("tailor_resume", "resume_tailor.tailor_resume", "调用 LLM 生成定制简历，失败进入 Trace。"),
                self._step("verify_resume", "guardrail.verify_resume", "验证新增事实、关键词覆盖和风险等级。"),
            ]
            react_loops = [
                {
                    "name": "tailor_verify_repair",
                    "max_iterations": 2,
                    "pattern": [
                        "Observe: 读取 JD 缺口和检索证据",
                        "Act: 生成或修复定制简历",
                        "Observe: Guardrail 验证风险等级",
                        "Act: 高风险时回退到证据更强的表述",
                    ],
                }
            ]
        elif task_type == "quick_apply":
            steps = [
                self._step("load_profile", "profile_repository.load_profile", "读取候选人档案。"),
                self._step("load_job", "job_repository.load_job", "读取目标岗位。"),
                self._step("ensure_resume_version", "resume_tailor.tailor_resume", "缺少定制简历时先生成。"),
                self._step(
                    "create_application_packet",
                    "application.create_quick_apply_packet",
                    "生成投递材料和人工确认清单。",
                ),
            ]
            react_loops = []
        else:
            steps = []
            react_loops = []

        return {
            "mode": "plan_execute",
            "task_type": task_type,
            "skills": active_skill_names_for_task(task_type),
            "subagents": subagents_for_task(task_type),
            "context_policy": self._context_policy(task_type),
            "steps": steps,
            "react_loops": react_loops,
            "tool_count": len(AGENT_TOOLS),
            "mcp_recommendation": self._mcp_recommendation(),
            "langgraph_decision": self._langgraph_decision(),
        }

    def _step(self, name: str, tool: str, purpose: str) -> dict[str, str]:
        return {"step": name, "tool": tool, "purpose": purpose}

    def _context_policy(self, task_type: str) -> dict[str, Any]:
        needs_llm_context = task_type in {"tailor_resume_for_job", "quick_apply"}
        return {
            "progressive_disclosure": needs_llm_context,
            "compression_strategy": "hierarchical_progressive_disclosure" if needs_llm_context else "not_required",
            "visible_layers": ["profile_facts", "job_requirements", "ranked_evidence"] if needs_llm_context else [],
            "deferred_layers": ["full_raw_resume", "full_raw_jd", "non_top_evidence_chunks"] if needs_llm_context else [],
            "failure_policy": "缺少证据时直接报告缺口，不让 LLM 编造。",
        }

    def _langgraph_decision(self) -> dict[str, Any]:
        return {
            "migrate_now": False,
            "reason": (
                "当前 Orchestrator 已经有显式 plan_execute、step trace、artifact 和注册工具边界；"
                "此阶段优先补齐 Skill、SubAgent、上下文压缩和评测闭环。"
            ),
            "migration_trigger": (
                "当出现多分支状态机、人工审批节点、后台长任务恢复、跨 MCP server 工具调用或复杂 retry policy 时，"
                "再迁移到 LangGraph 会更有工程收益。"
            ),
        }

    def _mcp_recommendation(self) -> dict[str, Any]:
        return {
            "needed_now": False,
            "reason": (
                "当前工具都在同一 FastAPI 进程内，直接 Python 调用更简单、可测、可追踪；"
                "当接入浏览器自动化、邮箱、日历、云盘或第三方招聘平台账号时，再把这些外部能力包装为 MCP。"
            ),
            "best_candidates": [
                "browser.apply_form_assist",
                "email.send_outreach",
                "calendar.schedule_interview",
                "job_board.authenticated_search",
            ],
        }
