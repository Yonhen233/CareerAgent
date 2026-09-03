from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from app.agents.skills import (
    active_skill_names_for_task,
    skill_contracts_for_task,
    skill_names_for_tool,
    validate_tool_permissions,
)
from app.agents.subagents import roles_for_task


T = TypeVar("T")


def _json_schema_for_type(type_name: str) -> dict[str, Any]:
    expression = str(type_name or "Any").strip()
    options = [item.strip() for item in expression.split("|") if item.strip()]
    nullable = "None" in options or "optional" in {item.lower() for item in options}
    options = [item for item in options if item not in {"None", "optional"}]
    if len(options) > 1 and all(item not in {"str", "int", "float", "bool", "dict", "list", "datetime", "Any"} for item in options):
        schema: dict[str, Any] = {"type": "string", "enum": options}
    elif len(options) > 1:
        schema = {"anyOf": [_json_schema_for_type(item) for item in options]}
    else:
        option = options[0] if options else "Any"
        primitive = {
            "str": {"type": "string"},
            "int": {"type": "integer"},
            "float": {"type": "number"},
            "bool": {"type": "boolean"},
            "dict": {"type": "object"},
            "list": {"type": "array"},
            "datetime": {"type": "string", "format": "date-time"},
            "Any": {},
        }
        if option.startswith("list[") and option.endswith("]"):
            schema = {"type": "array", "items": _json_schema_for_type(option[5:-1])}
        else:
            schema = primitive.get(option, {"type": "object", "x-python-type": option})
    if nullable:
        schema = {"anyOf": [schema, {"type": "null"}]}
    return schema


def _object_schema(fields: dict[str, str]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    alternative_requirements: list[dict[str, Any]] = []
    for field_name, type_name in fields.items():
        alternatives = [item.strip() for item in field_name.split("|") if item.strip()]
        alternatives = alternatives or [field_name]
        for alternative in alternatives:
            properties[alternative] = _json_schema_for_type(type_name)
        optional = "None" in type_name or "optional" in type_name.lower()
        if not optional and len(alternatives) == 1:
            required.append(alternatives[0])
        elif not optional:
            alternative_requirements.append(
                {"anyOf": [{"required": [alternative]} for alternative in alternatives]}
            )
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": True,
    }
    if alternative_requirements:
        schema["allOf"] = alternative_requirements
    return schema


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    purpose: str
    input_schema: dict[str, str]
    output_schema: dict[str, str]
    side_effects: list[str]
    execution_mode: str = "async"
    risk_level: str = "low"
    approval_requirement: str = "none"
    idempotency_policy: str = "read_only"
    timeout_seconds: int = 30
    retry_policy: dict[str, Any] = field(
        default_factory=lambda: {"max_attempts": 1, "retryable_errors": []}
    )
    retry_owner: str = "runtime"
    contract_version: str = "careeragent-tool-contract-v3"
    schema_version: str = "2020-12"
    audit_events: list[str] = field(default_factory=lambda: ["step_started", "step_completed", "step_failed"])
    mcp_candidate: bool = False
    invocation_scope: str = "direct"
    parent_tools: list[str] = field(default_factory=list)
    allow_extra_input: bool = False

    def as_dict(self) -> dict[str, Any]:
        input_json_schema = _object_schema(self.input_schema)
        input_json_schema["additionalProperties"] = self.allow_extra_input
        payload = {
            "name": self.name,
            "purpose": self.purpose,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "input_json_schema": input_json_schema,
            "output_json_schema": _object_schema(self.output_schema),
            "side_effects": self.side_effects,
            "execution_mode": self.execution_mode,
            "risk_level": self.risk_level,
            "approval_requirement": self.approval_requirement,
            "idempotency_policy": self.idempotency_policy,
            "replay_safety": self.replay_safety,
            "timeout_seconds": self.timeout_seconds,
            "retry_policy": self.retry_policy,
            "retry_owner": self.retry_owner,
            "contract_version": self.contract_version,
            "schema_version": self.schema_version,
            "audit_events": self.audit_events,
            "mcp_candidate": self.mcp_candidate,
            "invocation_scope": self.invocation_scope,
            "parent_tools": self.parent_tools,
            "allow_extra_input": self.allow_extra_input,
        }
        payload["allowed_skills"] = skill_names_for_tool(self.name)
        return payload

    @property
    def replay_safety(self) -> str:
        if not self.side_effects or self.idempotency_policy == "read_only":
            return "read_only"
        if "external_email_send" in self.side_effects or "optional_form_submission" in self.side_effects:
            return "non_replayable_without_new_human_decision"
        if self.approval_requirement != "none":
            return "approval_bound_idempotent"
        return "idempotent_with_business_key"


@dataclass(frozen=True)
class BoundAgentTool(Generic[T]):
    """Couple a registered tool contract with the only callable used for one invocation."""

    spec: AgentToolSpec
    handler: Callable[[], T | Awaitable[T]]
    binding_version: str = "careeragent-bound-tool-v1"


def bind_agent_tool(name: str, handler: Callable[[], T | Awaitable[T]]) -> BoundAgentTool[T]:
    if not callable(handler):
        raise TypeError(f"Tool {name} handler must be callable.")
    return BoundAgentTool(spec=get_agent_tool(name), handler=handler)


AGENT_TOOLS: list[AgentToolSpec] = [
    AgentToolSpec(
        name="LangGraph.AgentPlanner",
        purpose="把任务请求编译为受工具权限和完成条件约束的执行计划。",
        input_schema={"task_type": "str"},
        output_schema={"plan": "dict"},
        side_effects=[],
        timeout_seconds=10,
    ),
    AgentToolSpec(
        name="llm.intent_planner",
        purpose="把用户自然语言解析为受契约约束的求职任务计划。",
        input_schema={"instruction|error": "str", "plan": "dict|None"},
        output_schema={"plan": "dict"},
        side_effects=["llm_call", "llm_call_log"],
        risk_level="medium",
        timeout_seconds=180,
        retry_policy={"max_attempts": 1, "retryable_errors": []},
        retry_owner="handler",
    ),
    AgentToolSpec(
        name="NaturalLanguageAgentService",
        purpose="按已验证计划协调建档、找岗、定制、投递材料和面试准备子流程。",
        input_schema={"intent": "str"},
        output_schema={"result": "dict"},
        side_effects=["subgraph_execution", "sqlite_write", "optional_llm_call"],
        risk_level="medium",
        timeout_seconds=900,
        retry_policy={"max_attempts": 1, "retryable_errors": []},
        retry_owner="orchestrator",
    ),
    AgentToolSpec(
        name="profile_repository.load_profile",
        purpose="加载候选人档案和结构化简历。",
        input_schema={"profile_id": "int"},
        output_schema={"profile": "Profile"},
        side_effects=[],
        timeout_seconds=5,
    ),
    AgentToolSpec(
        name="resume_parser.create_profile_from_pdf",
        purpose="解析用户上传的 PDF 简历，持久化结构化 Profile 并建立可检索 Resume Chunk。",
        input_schema={"filename": "str", "file_artifact_id": "int|None"},
        output_schema={"profile": "Profile", "resume_chunks": "list[ResumeChunk]"},
        side_effects=["llm_call", "sqlite_write", "embedding_model_call", "artifact_write"],
        risk_level="medium",
        idempotency_policy="file_sha256+tenant_id",
        timeout_seconds=240,
        retry_policy={"max_attempts": 1, "retryable_errors": []},
        retry_owner="handler",
    ),
    AgentToolSpec(
        name="job_search.search_jobs",
        purpose="并发搜索真实招聘源并返回岗位列表。",
        input_schema={"query": "str", "location": "str|None", "limit": "int"},
        output_schema={"jobs": "list[Job]", "source_errors": "dict"},
        side_effects=["external_http_requests", "job_upsert"],
        risk_level="medium",
        idempotency_policy="source+external_id unique upsert",
        timeout_seconds=30,
        retry_policy={"max_attempts": 2, "retryable_errors": ["timeout", "connection_error", "5xx"]},
        mcp_candidate=True,
    ),
    AgentToolSpec(
        name="job_repository.load_job",
        purpose="加载目标岗位、原始 JD 和结构化 JD。",
        input_schema={"job_id": "int"},
        output_schema={"job": "Job"},
        side_effects=[],
        timeout_seconds=5,
    ),
    AgentToolSpec(
        name="job_repository.create_from_jd",
        purpose="解析原始 JD，持久化岗位并建立结构化与向量检索索引。",
        input_schema={"raw_jd_text": "str", "title": "str|None", "company": "str|None"},
        output_schema={"job": "Job", "job_chunks": "list[JobChunk]"},
        side_effects=["llm_call", "sqlite_write", "embedding_model_call"],
        risk_level="medium",
        idempotency_policy="source+external_id unique upsert",
        timeout_seconds=180,
        retry_policy={"max_attempts": 1, "retryable_errors": []},
        retry_owner="handler",
    ),
    AgentToolSpec(
        name="jd_parser.parse_jd",
        purpose="把原始 JD 解析成 required_skills、responsibilities、qualifications 等结构化字段。",
        input_schema={"raw_jd_text": "str", "title": "str|None", "company": "str|None"},
        output_schema={"structured_jd": "dict"},
        side_effects=["llm_call", "llm_call_log"],
        risk_level="medium",
        idempotency_policy="caller persists one structured JD per job version",
        timeout_seconds=90,
        retry_policy={"max_attempts": 2, "retryable_errors": ["timeout", "connection_error", "invalid_json"]},
        retry_owner="handler",
        invocation_scope="embedded",
        parent_tools=["job_search.search_jobs", "NaturalLanguageAgentService"],
    ),
    AgentToolSpec(
        name="vector_index.upsert_job_chunks",
        purpose="切分并存储职位 JD chunk，同时写入 embedding 和可选 Chroma 镜像。",
        input_schema={"job_id": "int", "chunks": "list[TextChunk]"},
        output_schema={"inserted": "int"},
        side_effects=["sqlite_write", "optional_chroma_write", "embedding_model_call"],
        risk_level="medium",
        idempotency_policy="job_id+chunk_uid unique upsert",
        timeout_seconds=120,
        retry_policy={"max_attempts": 1, "retryable_errors": []},
        invocation_scope="embedded",
        parent_tools=["job_search.search_jobs", "NaturalLanguageAgentService"],
    ),
    AgentToolSpec(
        name="matcher.match_job",
        purpose="计算岗位匹配分数、技能覆盖、缺口和可解释证据。",
        input_schema={"profile_id": "int", "job_id": "int", "reuse_match_result_id": "int|None"},
        output_schema={"match_result": "MatchResult"},
        side_effects=["sqlite_append", "embedding_model_call", "reranker_call"],
        idempotency_policy="append-only result; run keeps the selected match_result_id",
        timeout_seconds=120,
    ),
    AgentToolSpec(
        name="matcher.enforce_fit_gate",
        purpose="在高风险投递路径前依据既有 MatchResult 和证据质量执行适配度策略门禁。",
        input_schema={"profile_id": "int", "job_id": "int", "min_score": "float"},
        output_schema={"passed": "bool", "overall_score": "float", "min_score": "float"},
        side_effects=[],
        risk_level="medium",
        timeout_seconds=30,
    ),
    AgentToolSpec(
        name="vector_index.retrieve_resume_evidence",
        purpose="基于 JD 查询简历 chunk，一阶段 Top20 检索后用 reranker 二阶段排序。",
        input_schema={"profile_id": "int", "query": "str", "top_k": "int"},
        output_schema={"evidence_chunks": "list[RetrievedChunk]", "retrieval_quality": "dict"},
        side_effects=["embedding_model_call", "reranker_call"],
        timeout_seconds=120,
    ),
    AgentToolSpec(
        name="resume_tailor.tailor_resume",
        purpose="根据 JD 和 RAG 证据生成定制简历。",
        input_schema={"profile_id": "int", "job_id": "int"},
        output_schema={"resume_version": "ResumeVersion"},
        side_effects=["llm_call", "sqlite_write", "llm_call_log"],
        risk_level="medium",
        idempotency_policy="agent_run+profile_id+job_id",
        timeout_seconds=240,
        retry_policy={"max_attempts": 2, "retryable_errors": ["timeout", "connection_error", "guardrail_repair"]},
        retry_owner="handler",
    ),
    AgentToolSpec(
        name="guardrail.verify_resume",
        purpose="检查定制简历是否新增未经证据支持的事实，并计算关键词覆盖率。",
        input_schema={"profile_id": "int", "job_id": "int", "resume_version_id": "int"},
        output_schema={"passed": "bool", "risk_level": "str"},
        side_effects=[],
        timeout_seconds=10,
    ),
    AgentToolSpec(
        name="application.create_quick_apply_packet",
        purpose="生成投递包、求职信、外联文案、清单和投递链接。",
        input_schema={"profile_id": "int", "job_id": "int", "resume_version_id": "int", "approval_id": "int"},
        output_schema={"application": "Application"},
        side_effects=["sqlite_write", "llm_call", "llm_call_log"],
        risk_level="high",
        approval_requirement="application_packet",
        idempotency_policy="agent_run+profile_id+job_id+resume_version_id",
        timeout_seconds=180,
        retry_policy={"max_attempts": 1, "retryable_errors": []},
        audit_events=["approval_requested", "approval_decided", "step_completed", "step_failed"],
        mcp_candidate=True,
    ),
    AgentToolSpec(
        name="interview_prep.generate_packet",
        purpose="基于 JD、匹配结果和 RAG 证据生成面试问题、回答要点、缺口 drill 和调研清单。",
        input_schema={"profile_id": "int", "job_id": "int", "match_result_id": "int"},
        output_schema={"interview_prep": "InterviewPrep"},
        side_effects=["sqlite_write", "llm_call", "embedding_model_call", "reranker_call"],
        risk_level="medium",
        idempotency_policy="agent_run+profile_id+job_id",
        timeout_seconds=240,
        retry_policy={"max_attempts": 2, "retryable_errors": ["timeout", "connection_error"]},
        retry_owner="handler",
    ),
    AgentToolSpec(
        name="interview_experience.import_text",
        purpose="导入同岗面经文本，抽取真实问题、轮次、技术主题和可信度信号。",
        input_schema={"source_site": "str", "raw_text": "str", "job_id": "int|None"},
        output_schema={"interview_experience": "InterviewExperience"},
        side_effects=["sqlite_write"],
        risk_level="medium",
        idempotency_policy="source_url/content_hash deduplication",
        timeout_seconds=30,
        mcp_candidate=True,
        invocation_scope="embedded",
        parent_tools=["interview_prep.generate_packet"],
    ),
    AgentToolSpec(
        name="browser_apply",
        purpose="审批后使用 Playwright 填写或提交招聘页面。",
        input_schema={"url": "str", "fields": "dict", "submit_selector": "str|None", "approval_id": "int"},
        output_schema={"status": "filled|submitted", "final_url": "str", "filled_selectors": "list"},
        side_effects=["external_browser_write", "optional_form_submission"],
        execution_mode="sync",
        risk_level="high",
        approval_requirement="browser_apply",
        idempotency_policy="approval_id binds one audited execution",
        timeout_seconds=120,
        retry_policy={"max_attempts": 1, "retryable_errors": []},
        audit_events=["approval_decided", "browser_apply_tool_execution_released", "browser_apply_tool_execution_failed"],
        mcp_candidate=True,
    ),
    AgentToolSpec(
        name="email_draft",
        purpose="审批后生成可审阅的 EML 邮件草稿。",
        input_schema={"to": "str", "subject": "str", "body": "str", "approval_id": "int"},
        output_schema={"status": "draft_created", "draft_path": "str"},
        side_effects=["filesystem_write"],
        execution_mode="sync",
        risk_level="high",
        approval_requirement="email_draft",
        idempotency_policy="approval_id binds one audited execution",
        timeout_seconds=30,
        retry_policy={"max_attempts": 1, "retryable_errors": []},
        audit_events=["approval_decided", "email_draft_tool_execution_released", "email_draft_tool_execution_failed"],
        mcp_candidate=True,
    ),
    AgentToolSpec(
        name="email_send",
        purpose="审批后通过 SMTP 发送邮件。",
        input_schema={"to": "str", "subject": "str", "body": "str", "approval_id": "int"},
        output_schema={"status": "email_sent", "sent_at": "datetime"},
        side_effects=["external_email_send"],
        execution_mode="sync",
        risk_level="high",
        approval_requirement="email_send",
        idempotency_policy="approval_id binds one audited execution",
        timeout_seconds=45,
        retry_policy={"max_attempts": 1, "retryable_errors": []},
        audit_events=["approval_decided", "email_send_tool_execution_released", "email_send_tool_execution_failed"],
        mcp_candidate=True,
    ),
]

_TOOL_BY_NAME = {tool.name: tool for tool in AGENT_TOOLS}


def get_agent_tool(name: str) -> AgentToolSpec:
    try:
        return _TOOL_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unknown Agent tool: {name}") from exc


def list_agent_tools() -> list[dict[str, Any]]:
    return [tool.as_dict() for tool in AGENT_TOOLS]


def tool_policies_for_names(names: list[str]) -> list[dict[str, Any]]:
    return [get_agent_tool(name).as_dict() for name in dict.fromkeys(names)]


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
        elif task_type == "full_career_flow":
            steps = [
                self._step("load_profile", "profile_repository.load_profile", "读取候选人 Profile。"),
                self._step("search_jobs", "job_search.search_jobs", "搜索并入库中文真实岗位。"),
                self._step("match_and_select_job", "matcher.match_job", "完成匹配排序并等待用户选择目标岗位。"),
                self._step("retrieve_resume_evidence", "vector_index.retrieve_resume_evidence", "检索 Top evidence 支撑定制。"),
                self._step("tailor_resume", "resume_tailor.tailor_resume", "生成面向目标 JD 的定制简历。"),
                self._step("verify_resume", "guardrail.verify_resume", "验证事实边界和关键词覆盖。"),
                self._step("fit_gate", "matcher.enforce_fit_gate", "投递前检查适配度是否达到阈值。"),
                self._step(
                    "create_application_packet",
                    "application.create_quick_apply_packet",
                    "经人工确认后生成投递包。",
                ),
                self._step(
                    "generate_interview_prep",
                    "interview_prep.generate_packet",
                    "生成 JD+项目绑定的面试准备包。",
                ),
            ]
            react_loops = [
                {
                    "name": "tailor_verify_repair",
                    "max_iterations": 2,
                    "pattern": [
                        "Observe: 读取岗位缺口、证据类型和 Guardrail 问题",
                        "Act: 收缩上下文并重写定制简历",
                        "Observe: 再次验证风险等级",
                    ],
                }
            ]
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
                self._step("fit_gate", "matcher.enforce_fit_gate", "人工确认前执行适配度与证据门禁。"),
                self._step(
                    "create_application_packet",
                    "application.create_quick_apply_packet",
                    "经人工确认后生成投递材料。",
                ),
            ]
            react_loops = []
        elif task_type == "prepare_interview_for_job":
            steps = [
                self._step("load_profile", "profile_repository.load_profile", "读取候选人简历事实。"),
                self._step("load_job", "job_repository.load_job", "读取目标 JD 和结构化要求。"),
                self._step("match_job", "matcher.match_job", "生成匹配、缺口和 RAG 证据。"),
                self._step(
                    "generate_interview_prep",
                    "interview_prep.generate_packet",
                    "生成可追溯的中文面试准备包，不把缺口包装成已掌握。",
                ),
            ]
            react_loops = []
        else:
            steps = []
            react_loops = []

        tool_names = [step["tool"] for step in steps]
        permission_violations = validate_tool_permissions(task_type, tool_names)
        if permission_violations:
            raise ValueError(
                f"Skill tool permission denied for task {task_type}: {', '.join(permission_violations)}"
            )
        return {
            "mode": "plan_execute",
            "orchestration_framework": "langgraph",
            "task_type": task_type,
            "skills": active_skill_names_for_task(task_type),
            "skill_contracts": skill_contracts_for_task(task_type),
            "skill_disclosure": {
                "catalog_in_plan": True,
                "instructions_in_plan": False,
                "detail_endpoint": "/agent/skills/{skill_name}",
            },
            "roles": roles_for_task(task_type),
            "subagents": roles_for_task(task_type),  # Deprecated response compatibility.
            "context_policy": self._context_policy(task_type),
            "steps": steps,
            "tool_policies": tool_policies_for_names(tool_names),
            "tool_permission_validation": {
                "passed": True,
                "checked_tool_count": len(set(tool_names)),
                "violations": [],
            },
            "react_loops": react_loops,
            "tool_count": len(AGENT_TOOLS),
            "mcp_recommendation": self._mcp_recommendation(),
            "langgraph_decision": self._langgraph_decision(),
        }

    def _step(self, name: str, tool: str, purpose: str) -> dict[str, str]:
        return {"step": name, "tool": tool, "purpose": purpose}

    def _context_policy(self, task_type: str) -> dict[str, Any]:
        needs_llm_context = task_type in {
            "tailor_resume_for_job",
            "quick_apply",
            "prepare_interview_for_job",
            "full_career_flow",
        }
        return {
            "progressive_disclosure": needs_llm_context,
            "compression_strategy": "progressive_disclosure_budgeted_packet" if needs_llm_context else "not_required",
            "visible_layers": ["profile_facts", "job_requirements", "ranked_evidence"] if needs_llm_context else [],
            "deferred_layers": ["full_raw_resume", "full_raw_jd", "non_top_evidence_chunks"] if needs_llm_context else [],
            "failure_policy": "缺少证据时直接报告缺口，不让 LLM 编造。",
            "implementation": "LLM 调用前的 runtime policy，不作为独立 subagent 或可调用 skill。",
        }

    def _langgraph_decision(self) -> dict[str, Any]:
        return {
            "migrate_now": True,
            "migrated": True,
            "reason": (
                "主 AgentOrchestrator 已迁移为 LangGraph StateGraph，旧类名只作为兼容外壳；"
                "FastAPI、自然语言入口和评测都通过 LangGraph 图执行。"
            ),
            "migration_trigger": (
                "当前已接入 SQLite checkpointer、投递前 interrupt、后台 queued run 和 SSE 事件流；"
                "高风险工具继续通过统一 Tool Policy 和 approval table 执行。"
            ),
        }

    def _mcp_recommendation(self) -> dict[str, Any]:
        return {
            "needed_now": False,
            "reason": (
                "当前业务工具仍在同一服务边界，直接 Python 调用更容易测试和审计；"
                "只有浏览器、邮箱、日历或招聘平台账号被拆成独立进程/服务时才引入 MCP。"
            ),
            "best_candidates": ["browser_apply", "email_draft", "email_send", "job_board.authenticated_search"],
        }
