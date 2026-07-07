from __future__ import annotations

import hashlib
import json
import time
from typing import Any, TypedDict
from uuid import uuid4

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agents.orchestrator import AgentOrchestrator
from app.core.config import get_settings
from app.core.llm import LLMClient, llm_trace_context
from app.models.entities import AgentArtifact, Job, Profile
from app.models.schemas import AgentRunRequest, GuidedProfileRequest, NaturalLanguageAgentRequest
from app.services.jd_parser import JDParserService
from app.services.resume_parser import ResumeParserService
from app.services.text_splitter import ResumeTextSplitter
from app.services.trace_service import TraceService
from app.services.vector_index import SQLiteVectorIndex


INTENTS = {
    "create_profile",
    "update_profile",
    "search_jobs",
    "tailor_resume",
    "quick_apply",
    "interview_prep",
    "full_flow",
}


class NaturalLanguageGraphState(TypedDict, total=False):
    request: dict[str, Any]
    run_id: int
    graph_thread_id: str
    plan: dict[str, Any]
    result: dict[str, Any]
    execution_error: str | None
    repair_attempts: list[dict[str, Any]]
    output: dict[str, Any]


class NaturalLanguageAgentService:
    def __init__(
        self,
        *,
        llm: LLMClient | None = None,
        trace: TraceService | None = None,
        orchestrator: AgentOrchestrator | None = None,
    ) -> None:
        self.llm = llm or LLMClient()
        self.trace = trace or TraceService()
        self.orchestrator = orchestrator or AgentOrchestrator()
        self.resume_parser = ResumeParserService()
        self.jd_parser = JDParserService()
        self.splitter = ResumeTextSplitter()
        self.vector_index = SQLiteVectorIndex()
        self.settings = get_settings()
        self._runtime_dbs: dict[int, Session] = {}
        self._checkpoint_conn = None
        self.checkpointer = None
        self._graph = None

    async def run(self, db: Session, request: NaturalLanguageAgentRequest) -> dict[str, Any]:
        started = time.perf_counter()
        graph_thread_id = f"natural-run-{uuid4().hex}"
        run = self.trace.create_run(
            db,
            task_type="natural_language_request",
            profile_id=request.profile_id,
            job_id=request.job_id,
            input_json={
                **request.model_dump(),
                "orchestration_framework": "langgraph",
                "graph_thread_id": graph_thread_id,
            },
        )
        self._runtime_dbs[run.id] = db
        self.trace.add_event(
            db,
            run_id=run.id,
            event_type="run_started",
            payload={"task_type": "natural_language_request", "graph_thread_id": graph_thread_id},
        )
        try:
            graph = await self._ensure_graph()
            final_state = await self._invoke_graph(
                graph,
                {
                    "request": request.model_dump(),
                    "run_id": run.id,
                    "graph_thread_id": graph_thread_id,
                    "repair_attempts": [],
                },
                db=db,
                run_id=run.id,
                config={"configurable": {"thread_id": graph_thread_id}},
            )
            payload = dict(final_state.get("output") or {})
            payload["orchestration_framework"] = "langgraph"
            payload["graph_thread_id"] = graph_thread_id
            response_status = str(payload.get("status") or "completed")
            error_message = (payload.get("result_json") or {}).get("error") if response_status == "failed" else None
            return self.trace.finish_run(
                db,
                run=run,
                status=response_status,
                output_json=payload,
                error_message=error_message,
                started_at=started,
            )
        except Exception as exc:  # noqa: BLE001
            payload = {
                "run_id": run.id,
                "status": "failed",
                "user_message": f"处理失败：{self._public_error_message(exc)}",
                "plan_json": {},
                "result_json": {"error": str(exc)},
                "repair_attempts": [],
                "orchestration_framework": "langgraph",
                "graph_thread_id": graph_thread_id,
            }
            return self.trace.finish_run(
                db,
                run=run,
                status="failed",
                output_json=payload,
                error_message=str(exc),
                started_at=started,
            )
        finally:
            self._runtime_dbs.pop(run.id, None)
            await self._close_checkpoint()

    def _build_graph(self):
        graph = StateGraph(NaturalLanguageGraphState)
        graph.add_node("parse_user_request", self._node_parse_user_request)
        graph.add_node("execute_user_plan", self._node_execute_user_plan)
        graph.add_node("repair_user_plan", self._node_repair_user_plan)
        graph.add_node("execute_repaired_user_plan", self._node_execute_repaired_user_plan)
        graph.add_node("finalize_success", self._node_finalize_success)
        graph.add_node("finalize_failed", self._node_finalize_failed)
        graph.add_edge(START, "parse_user_request")
        graph.add_edge("parse_user_request", "execute_user_plan")
        graph.add_conditional_edges(
            "execute_user_plan",
            self._route_after_execute,
            {"repair_user_plan": "repair_user_plan", "finalize_success": "finalize_success"},
        )
        graph.add_edge("repair_user_plan", "execute_repaired_user_plan")
        graph.add_conditional_edges(
            "execute_repaired_user_plan",
            self._route_after_repaired_execute,
            {"finalize_success": "finalize_success", "finalize_failed": "finalize_failed"},
        )
        graph.add_edge("finalize_success", END)
        graph.add_edge("finalize_failed", END)
        return graph.compile(checkpointer=self.checkpointer)

    async def _ensure_graph(self):
        if self._graph is not None:
            return self._graph
        path = self.settings.langgraph_checkpoint_path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_conn = await aiosqlite.connect(str(path))
        self.checkpointer = AsyncSqliteSaver(self._checkpoint_conn)
        await self.checkpointer.setup()
        self._graph = self._build_graph()
        return self._graph

    async def _close_checkpoint(self) -> None:
        if self._checkpoint_conn is not None:
            await self._checkpoint_conn.close()
        self._checkpoint_conn = None
        self.checkpointer = None
        self._graph = None

    async def _invoke_graph(
        self,
        graph,
        payload: dict[str, Any],
        *,
        db: Session,
        run_id: int,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            async for event in graph.astream_events(payload, config=config, version="v2"):
                self._record_langgraph_event(db, run_id=run_id, event=event)
            snapshot = await graph.aget_state(config)
            state = dict(snapshot.values or {})
            if snapshot.config:
                state["checkpoint_id"] = (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
            return state
        except Exception as exc:
            self.trace.add_event(
                db,
                run_id=run_id,
                event_type="graph_failed",
                payload={"error": str(exc), "error_type": exc.__class__.__name__},
            )
            raise

    async def _node_parse_user_request(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        request = NaturalLanguageAgentRequest(**state["request"])
        db = self._db_from_state(state)
        plan = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="parse_user_request",
            tool_name="llm.intent_planner",
            input_json={"instruction": request.instruction},
            handler=lambda: self._build_plan(db, request),
        )
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="natural_language_plan", payload=plan)
        return {"plan": plan}

    async def _node_execute_user_plan(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        request = NaturalLanguageAgentRequest(**state["request"])
        db = self._db_from_state(state)
        plan = state.get("plan") or {}
        try:
            result = await self.trace.step(
                db,
                run_id=state["run_id"],
                step_name="execute_user_plan",
                tool_name="NaturalLanguageAgentService",
                input_json={"intent": plan.get("intent")},
                handler=lambda: self._execute_plan(db, request, plan),
            )
            return {"result": result, "execution_error": None}
        except Exception as exc:  # noqa: BLE001
            return {"execution_error": str(exc)}

    async def _node_repair_user_plan(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        request = NaturalLanguageAgentRequest(**state["request"])
        db = self._db_from_state(state)
        plan = state.get("plan") or {}
        error = RuntimeError(state.get("execution_error") or "执行失败")
        repaired_plan = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="repair_user_plan",
            tool_name="llm.intent_planner",
            input_json={"error": str(error), "plan": plan},
            handler=lambda: self._repair_plan(db, request, plan, error),
        )
        repair_attempts = [
            *(state.get("repair_attempts") or []),
            {"error": str(error), "repaired_intent": repaired_plan.get("intent")},
        ]
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="natural_language_repaired_plan",
            payload=repaired_plan,
        )
        return {"plan": repaired_plan, "repair_attempts": repair_attempts, "execution_error": None}

    async def _node_execute_repaired_user_plan(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        request = NaturalLanguageAgentRequest(**state["request"])
        db = self._db_from_state(state)
        plan = state.get("plan") or {}
        try:
            result = await self.trace.step(
                db,
                run_id=state["run_id"],
                step_name="execute_repaired_user_plan",
                tool_name="NaturalLanguageAgentService",
                input_json={"intent": plan.get("intent")},
                handler=lambda: self._execute_plan(db, request, plan),
            )
            return {"result": result, "execution_error": None}
        except Exception as exc:  # noqa: BLE001
            return {"execution_error": str(exc), "result": {"error": str(exc)}}

    async def _node_finalize_success(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        plan = state.get("plan") or {}
        result = state.get("result") or {}
        response_status = "waiting_for_confirmation" if result.get("requires_confirmation") else "completed"
        payload = {
            "run_id": state["run_id"],
            "status": response_status,
            "user_message": self._user_message(plan, result),
            "plan_json": plan,
            "result_json": result,
            "repair_attempts": state.get("repair_attempts") or [],
        }
        return {"output": payload}

    async def _node_finalize_failed(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        error = state.get("execution_error") or "未知错误"
        payload = {
            "run_id": state["run_id"],
            "status": "failed",
            "user_message": f"处理失败：{self._public_error_message(RuntimeError(error))}",
            "plan_json": state.get("plan") or {},
            "result_json": {"error": error},
            "repair_attempts": state.get("repair_attempts") or [],
        }
        return {"output": payload}

    def _route_after_execute(self, state: NaturalLanguageGraphState) -> str:
        return "repair_user_plan" if state.get("execution_error") else "finalize_success"

    def _route_after_repaired_execute(self, state: NaturalLanguageGraphState) -> str:
        return "finalize_failed" if state.get("execution_error") else "finalize_success"

    def _db_from_state(self, state: NaturalLanguageGraphState) -> Session:
        db = self._runtime_dbs.get(int(state["run_id"]))
        if db is None:
            raise RuntimeError("Natural language LangGraph state is missing the active database session.")
        return db

    def _record_langgraph_event(self, db: Session, *, run_id: int, event: dict[str, Any]) -> None:
        event_name = str(event.get("event") or "")
        node_name = str(event.get("name") or "")
        data = event.get("data") or {}
        if event_name == "on_chain_start":
            event_type = "graph_started" if node_name == "LangGraph" else "graph_node_started"
        elif event_name == "on_chain_end":
            event_type = "graph_completed" if node_name == "LangGraph" else "graph_node_completed"
        elif event_name == "on_chain_stream":
            event_type = "graph_update" if node_name == "LangGraph" else "graph_node_update"
        else:
            return
        self.trace.add_event(
            db,
            run_id=run_id,
            event_type=event_type,
            node_name=None if node_name == "LangGraph" else node_name,
            payload={
                "langgraph_event": event_name,
                "node_name": node_name,
                "data": self._json_safe_graph_value(data),
            },
        )

    def _json_safe_graph_value(self, value: Any) -> Any:
        if hasattr(value, "id") and hasattr(value, "value"):
            return {"id": str(getattr(value, "id")), "value": self.trace._json_safe(getattr(value, "value"))}
        if isinstance(value, tuple):
            return [self._json_safe_graph_value(item) for item in value]
        if isinstance(value, list):
            return [self._json_safe_graph_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._json_safe_graph_value(item) for key, item in value.items()}
        return self.trace._json_safe(value)

    async def _build_plan(self, db: Session, request: NaturalLanguageAgentRequest) -> dict[str, Any]:
        system_prompt = (
            "你是中文求职助手 Agent 的意图规划器。只返回 JSON。"
            "不要编造用户没有提供的经历；缺少必要 ID 时优先使用用户文本生成 profile 或 job。"
        )
        user_prompt = f"""
根据用户需求生成可执行计划。

可选 intent:
- create_profile: 根据用户自然语言生成简历档案
- update_profile: 修改已有简历档案并生成新档案
- search_jobs: 根据简历搜索岗位
- tailor_resume: 根据岗位/JD 定制简历
- quick_apply: 生成投递包
- interview_prep: 生成面试准备包
- full_flow: 建档/找岗/定制/投递包/面试包完整流程

重要约束:
- 如果用户明确说“不要投递 / 不投递 / 不要申请 / 只改简历 / 只生成面试准备”，不要选择 quick_apply 或 full_flow。
- 如果用户同时要求“改简历”和“面试准备”，intent 可选 interview_prep，并在 actions 中同时写入 tailor_resume 与 interview_prep。
- full_flow 只用于用户明确要求一键完整流程或包含投递材料。

返回 JSON schema:
{{
  "intent": "create_profile|update_profile|search_jobs|tailor_resume|quick_apply|interview_prep|full_flow",
  "query": string|null,
  "profile": {{
    "name": string,
    "email": string|null,
    "phone": string|null,
    "headline": string|null,
    "target_roles": [string],
    "skills": [string],
    "projects": [{{"name": string, "description": string, "tech_stack": [string], "impact": string}}],
    "work_experience": [{{"company": string, "role": string, "duration": string, "details": string, "tech_stack": [string]}}],
    "education": [{{"school": string, "degree": string, "major": string, "duration": string, "details": string}}],
    "awards": [string],
    "languages": [string]
  }}|null,
  "job": {{"title": string|null, "company": string|null, "location": string|null, "apply_url": string|null, "jd_text": string|null}}|null,
  "needs_profile": boolean,
  "needs_job": boolean,
  "actions": [string],
  "reason": string
}}

上下文:
profile_id={request.profile_id}
job_id={request.job_id}
resume_version_id={request.resume_version_id}
query={request.query}
location={request.location}
jd_text={request.jd_text or ""}

用户需求:
{request.instruction}
"""
        with llm_trace_context(stage="natural_language_plan", agent_run_task="natural_language_request"):
            plan = await self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.05,
                db=db,
                trace_name="natural_language.plan",
            )
        return self._normalize_plan(plan, request)

    async def _repair_plan(
        self,
        db: Session,
        request: NaturalLanguageAgentRequest,
        plan: dict[str, Any],
        error: Exception,
    ) -> dict[str, Any]:
        system_prompt = "你是 Agent 计划修复器。只返回 JSON；不得绕过事实校验、投递门禁或人工确认边界。"
        user_prompt = f"""
原计划执行失败，请基于错误修复一次计划。

错误:
{error}

原计划:
{json.dumps(plan, ensure_ascii=False)}

用户需求:
{request.instruction}

可用上下文:
profile_id={request.profile_id}
job_id={request.job_id}
jd_text={request.jd_text or ""}
query={request.query}

如果缺少 job_id 但有 JD，请设置 job.jd_text。
如果用户想完整处理但缺少岗位，请使用 full_flow。
如果用户明确不要投递或不要申请，不要使用 full_flow/quick_apply；可改为 tailor_resume 和 interview_prep。
如果是投递匹配分不足，不要绕过 fit_gate，可改为生成定制简历和面试准备建议。
返回与原计划相同 JSON schema。
"""
        with llm_trace_context(stage="natural_language_repair", agent_run_task="natural_language_request"):
            repaired = await self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.05,
                db=db,
                trace_name="natural_language.repair_plan",
            )
        return self._normalize_plan(repaired, request)

    async def _execute_plan(
        self,
        db: Session,
        request: NaturalLanguageAgentRequest,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        intent = plan["intent"]
        profile = self._resolve_profile(db, request.profile_id)
        if intent == "update_profile":
            profile = self._create_profile_from_plan(db, plan, base_profile=profile)
        elif profile is None and plan.get("profile"):
            profile = self._create_profile_from_plan(db, plan, base_profile=None)

        job = self._resolve_job(db, request.job_id)
        if job is None and (request.jd_text or (plan.get("job") or {}).get("jd_text")):
            job = await self._create_job_from_plan(db, request, plan)

        result: dict[str, Any] = {
            "profile": self._profile_payload(profile),
            "job": self._job_payload(job),
            "agent_runs": [],
        }

        if intent in {"create_profile", "update_profile"}:
            if profile is None:
                raise ValueError("需要简历信息才能生成简历档案。")
            return result

        if intent == "search_jobs":
            profile = self._require_profile(profile)
            run = await self.orchestrator.run(
                db,
                AgentRunRequest(
                    task_type="find_jobs_for_profile",
                    profile_id=profile.id,
                    query=plan.get("query") or request.query,
                    location=request.location,
                    limit=request.limit,
                ),
            )
            self._assert_run_completed(run, "岗位搜索")
            result["agent_runs"].append(self._run_payload(run))
            result["matches"] = (run.output_json or {}).get("matches", [])
            self._assert_search_has_matches(run)
            return result

        if intent == "full_flow":
            profile = self._require_profile(profile)
            run_request = AgentRunRequest(
                task_type="full_career_flow",
                profile_id=profile.id,
                job_id=job.id if job else None,
                query=plan.get("query") or request.query,
                location=request.location,
                limit=request.limit,
            )
            run = await self.orchestrator.run(
                db,
                run_request,
            )
            result["agent_runs"].append(self._run_payload(run))
            if run.status == "waiting_for_confirmation":
                result["requires_confirmation"] = run.output_json
                result["full_flow"] = run.output_json
                return result
            self._assert_run_completed(run, "完整流程")
            result["full_flow"] = run.output_json
            result["tailor"] = (run.output_json or {}).get("tailor")
            result["application"] = (run.output_json or {}).get("application")
            result["interview_prep"] = (run.output_json or {}).get("interview_prep")
            return result

        wants_tailor = intent in {"tailor_resume", "quick_apply"} or self._requests_tailor(request.instruction, plan)
        wants_interview = intent == "interview_prep" or self._requests_interview(request.instruction, plan)

        if intent in {"tailor_resume", "quick_apply", "interview_prep"} or wants_tailor or wants_interview:
            profile = self._require_profile(profile)
            job = self._require_job(job)

        if wants_tailor:
            tailor_run = await self.orchestrator.run(
                db,
                AgentRunRequest(task_type="tailor_resume_for_job", profile_id=profile.id, job_id=job.id),
            )
            self._assert_run_completed(tailor_run, "定制简历")
            result["tailor"] = self._completed_run_output(
                db,
                tailor_run,
                artifact_type="tailored_resume",
                required_key="resume_version_id",
            )
            result["agent_runs"].append(self._run_payload(tailor_run))
        if intent == "quick_apply":
            apply_run = await self.orchestrator.run(
                db,
                AgentRunRequest(
                    task_type="quick_apply",
                    profile_id=profile.id,
                    job_id=job.id,
                    resume_version_id=(result.get("tailor") or {}).get("resume_version_id") or request.resume_version_id,
                ),
            )
            result["agent_runs"].append(self._run_payload(apply_run))
            if apply_run.status == "waiting_for_confirmation":
                result["requires_confirmation"] = apply_run.output_json
                result["application"] = apply_run.output_json
                return result
            self._assert_run_completed(apply_run, "投递包")
            result["application"] = apply_run.output_json
        if wants_interview:
            interview_run = await self.orchestrator.run(
                db,
                AgentRunRequest(task_type="prepare_interview_for_job", profile_id=profile.id, job_id=job.id),
            )
            self._assert_run_completed(interview_run, "面试准备")
            result["interview_prep"] = self._completed_run_output(
                db,
                interview_run,
                artifact_type="interview_prep",
                required_key="interview_prep_id",
            )
            result["agent_runs"].append(self._run_payload(interview_run))
        return result

    def _normalize_plan(self, plan: dict[str, Any], request: NaturalLanguageAgentRequest) -> dict[str, Any]:
        intent = str(plan.get("intent") or "").strip()
        if intent not in INTENTS:
            intent = self._heuristic_intent(request.instruction)
        if self._forbids_application(request.instruction) and intent in {"quick_apply", "full_flow"}:
            intent = "interview_prep" if self._text_wants_interview(request.instruction) else "tailor_resume"
        normalized = {
            "intent": intent,
            "query": plan.get("query") or request.query or "Agent 开发实习生",
            "profile": plan.get("profile") if isinstance(plan.get("profile"), dict) else None,
            "job": plan.get("job") if isinstance(plan.get("job"), dict) else None,
            "needs_profile": bool(plan.get("needs_profile", intent != "create_profile")),
            "needs_job": bool(plan.get("needs_job", intent in {"tailor_resume", "quick_apply", "interview_prep"})),
            "actions": [str(item) for item in plan.get("actions", []) if str(item).strip()],
            "reason": str(plan.get("reason") or ""),
        }
        if self._forbids_application(request.instruction):
            normalized["actions"] = [
                action
                for action in normalized["actions"]
                if action not in {"quick_apply", "full_flow", "application_packet", "apply", "submit_application"}
            ]
            if self._text_wants_tailor(request.instruction) and "tailor_resume" not in normalized["actions"]:
                normalized["actions"].append("tailor_resume")
            if self._text_wants_interview(request.instruction) and "interview_prep" not in normalized["actions"]:
                normalized["actions"].append("interview_prep")
        if request.jd_text:
            normalized["job"] = {**(normalized["job"] or {}), "jd_text": request.jd_text}
        return normalized

    def _heuristic_intent(self, instruction: str) -> str:
        text = instruction.lower()
        if self._forbids_application(instruction):
            if self._text_wants_interview(instruction):
                return "interview_prep"
            if self._text_wants_tailor(instruction):
                return "tailor_resume"
        if any(word in text for word in ["一键", "全流程", "投递", "申请"]):
            return "full_flow"
        if any(word in text for word in ["面试", "八股", "追问"]):
            return "interview_prep"
        if any(word in text for word in ["改简历", "优化简历", "定制简历"]):
            return "tailor_resume"
        if any(word in text for word in ["找岗位", "搜索岗位", "推荐岗位"]):
            return "search_jobs"
        return "create_profile"

    def _forbids_application(self, instruction: str) -> bool:
        text = instruction.lower()
        negative_markers = [
            "不要投递",
            "不投递",
            "无需投递",
            "不用投递",
            "先不投递",
            "不要申请",
            "不申请",
            "无需申请",
            "不用申请",
            "不要生成投递",
            "不要投递包",
            "不要外发",
            "不要发送",
            "don't apply",
            "do not apply",
            "no application",
        ]
        return any(marker in text for marker in negative_markers)

    def _text_wants_tailor(self, instruction: str) -> bool:
        text = instruction.lower()
        return any(word in text for word in ["改简历", "修改简历", "优化简历", "定制简历", "tailor resume"])

    def _text_wants_interview(self, instruction: str) -> bool:
        text = instruction.lower()
        return any(word in text for word in ["面试", "八股", "追问", "interview"])

    def _requests_tailor(self, instruction: str, plan: dict[str, Any]) -> bool:
        actions = {str(action).strip() for action in plan.get("actions", [])}
        return "tailor_resume" in actions or self._text_wants_tailor(instruction)

    def _requests_interview(self, instruction: str, plan: dict[str, Any]) -> bool:
        actions = {str(action).strip() for action in plan.get("actions", [])}
        return "interview_prep" in actions or self._text_wants_interview(instruction)

    def _create_profile_from_plan(
        self,
        db: Session,
        plan: dict[str, Any],
        *,
        base_profile: Profile | None,
    ) -> Profile:
        profile_data = dict(base_profile.structured_profile_json or {}) if base_profile else {}
        profile_data.update(plan.get("profile") or {})
        if not profile_data.get("name"):
            profile_data["name"] = base_profile.name if base_profile else "候选人"
        payload = GuidedProfileRequest.model_validate(
            {
                "name": profile_data.get("name") or "候选人",
                "email": profile_data.get("email"),
                "phone": profile_data.get("phone"),
                "headline": profile_data.get("headline") or "Agent 开发实习生候选人",
                "target_roles": profile_data.get("target_roles") or ["Agent 开发实习生"],
                "skills": profile_data.get("skills") or [],
                "projects": profile_data.get("projects") or [],
                "work_experience": profile_data.get("work_experience") or [],
                "education": profile_data.get("education") or [],
                "awards": profile_data.get("awards") or [],
                "languages": profile_data.get("languages") or [],
            }
        )
        return self.resume_parser.create_profile_from_guided_answers(db, payload)

    async def _create_job_from_plan(
        self,
        db: Session,
        request: NaturalLanguageAgentRequest,
        plan: dict[str, Any],
    ) -> Job:
        job_data = plan.get("job") or {}
        jd_text = request.jd_text or job_data.get("jd_text")
        if not jd_text or len(str(jd_text).strip()) < 20:
            raise ValueError("需要提供岗位 JD 才能创建目标岗位。")
        title = self._clean_job_title(
            job_data.get("title"),
            jd_text=str(jd_text),
            fallback=plan.get("query") or request.query or "目标岗位",
        )
        company = job_data.get("company")
        location = self._clean_location(job_data.get("location"), fallback=request.location)
        structured = await self.jd_parser.parse_jd(
            str(jd_text),
            title=title,
            company=company,
            location=location,
            db=db,
        )
        external_id = f"natural:{hashlib.sha1(str(jd_text).encode('utf-8')).hexdigest()}"
        existing = db.query(Job).filter(Job.source == "natural_language", Job.external_id == external_id).first()
        if existing:
            existing.title = title
            existing.company = company
            existing.location = location
            existing.apply_url = job_data.get("apply_url")
            existing.raw_jd_text = str(jd_text)
            existing.structured_jd_json = structured
            db.commit()
            db.refresh(existing)
            self._index_job_chunks(db, existing)
            return existing
        job = Job(
            source="natural_language",
            external_id=external_id,
            title=title,
            company=company,
            location=location,
            apply_url=job_data.get("apply_url"),
            raw_jd_text=str(jd_text),
            structured_jd_json=structured,
            source_payload_json={"created_by": "natural_language_request"},
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        self._index_job_chunks(db, job)
        return job

    def _clean_job_title(self, title: Any, *, jd_text: str, fallback: str) -> str:
        title_text = str(title or "").strip()
        jd_title = self._title_from_jd(jd_text)
        if jd_title:
            return jd_title[:80]
        if (
            not title_text
            or len(title_text) > 60
            or "，" in title_text
            or "支持" in title_text
            or "技术栈" in title_text
        ):
            title_text = jd_title or fallback
        return title_text[:80] or "目标岗位"

    def _title_from_jd(self, jd_text: str) -> str | None:
        for raw_line in jd_text.splitlines()[:4]:
            line = raw_line.strip()
            if not line:
                continue
            for prefix in ["岗位：", "职位：", "岗位:", "职位:", "Title:", "Role:"]:
                if line.startswith(prefix):
                    value = line.replace(prefix, "", 1).strip()
                    return value[:80] if value else None
        return None

    def _clean_location(self, location: Any, *, fallback: str | None) -> str | None:
        text = str(location or "").strip()
        invalid_markers = ["?", "？", "未知", "unknown", "n/a", "null"]
        if not text or any(marker in text.lower() for marker in invalid_markers) or len(text) > 40:
            return fallback
        return text

    def _index_job_chunks(self, db: Session, job: Job) -> None:
        chunks = self.splitter.split_jd_text(job.raw_jd_text, job.structured_jd_json or {}, prefix=f"job_{job.id}")
        self.vector_index.upsert_job_chunks(db, job.id, chunks)

    def _resolve_profile(self, db: Session, profile_id: int | None) -> Profile | None:
        if not profile_id:
            return None
        return db.query(Profile).filter(Profile.id == profile_id).first()

    def _resolve_job(self, db: Session, job_id: int | None) -> Job | None:
        if not job_id:
            return None
        return db.query(Job).filter(Job.id == job_id).first()

    def _require_profile(self, profile: Profile | None) -> Profile:
        if profile is None:
            raise ValueError("需要先提供简历档案或在自然语言中描述简历经历。")
        return profile

    def _require_job(self, job: Job | None) -> Job:
        if job is None:
            raise ValueError("需要提供 Job ID、粘贴 JD，或要求 Agent 先搜索岗位。")
        return job

    def _assert_run_completed(self, run, label: str) -> None:
        if run.status != "completed":
            message = run.error_message or (run.output_json or {}).get("error") or "未知错误"
            raise ValueError(f"{label}失败：{message}")

    def _assert_search_has_matches(self, run) -> None:
        output = run.output_json or {}
        if output.get("matches"):
            return
        source_errors = output.get("source_errors") or {}
        if source_errors:
            detail = "；".join(f"{source}: {message}" for source, message in source_errors.items())
            raise ValueError(f"岗位搜索没有返回可推荐岗位，岗位源错误：{detail}")
        raise ValueError("岗位搜索没有返回可推荐岗位，请调整关键词、城市，或粘贴目标 JD 后重试。")

    def _public_error_message(self, exc: Exception) -> str:
        message = str(exc)
        if "LLM_API_KEY" in message or "LLM_BASE_URL" in message:
            return "模型服务未配置，暂时无法调用 LLM。请在控制台或部署环境配置模型 API 后重试。"
        return message

    def _profile_payload(self, profile: Profile | None) -> dict[str, Any] | None:
        if profile is None:
            return None
        return {"id": profile.id, "name": profile.name, "headline": profile.headline}

    def _job_payload(self, job: Job | None) -> dict[str, Any] | None:
        if job is None:
            return None
        return {"id": job.id, "title": job.title, "company": job.company, "location": job.location}

    def _run_payload(self, run) -> dict[str, Any]:
        return {
            "id": run.id,
            "task_type": run.task_type,
            "status": run.status,
            "output_json": run.output_json or {},
        }

    def _completed_run_output(
        self,
        db: Session,
        run,
        *,
        artifact_type: str,
        required_key: str,
    ) -> dict[str, Any]:
        output = dict(run.output_json or {})
        if output.get(required_key):
            return output
        artifact = (
            db.query(AgentArtifact)
            .filter(AgentArtifact.run_id == run.id, AgentArtifact.artifact_type == artifact_type)
            .order_by(AgentArtifact.id.desc())
            .first()
        )
        if artifact is None or not isinstance(artifact.artifact_json, dict):
            return output
        enriched = {**artifact.artifact_json, **output}
        if enriched != output:
            run.output_json = enriched
            db.add(run)
            db.commit()
            db.refresh(run)
        return enriched

    def _user_message(self, plan: dict[str, Any], result: dict[str, Any]) -> str:
        intent = plan.get("intent")
        pieces = []
        if result.get("profile"):
            pieces.append(f"简历档案 #{result['profile']['id']}")
        if result.get("job"):
            pieces.append(f"岗位 #{result['job']['id']}")
        if result.get("tailor"):
            resume_version_id = result["tailor"].get("resume_version_id")
            pieces.append(f"定制简历 #{resume_version_id}" if resume_version_id else "定制简历已完成")
        if result.get("application"):
            application_id = result["application"].get("application_id")
            pieces.append(f"投递包 #{application_id}" if application_id else "投递包等待确认")
        if result.get("interview_prep"):
            interview_prep_id = result["interview_prep"].get("interview_prep_id")
            pieces.append(f"面试包 #{interview_prep_id}" if interview_prep_id else "面试包已完成")
        if result.get("matches"):
            pieces.append(f"推荐岗位 {len(result['matches'])} 个")
        if result.get("full_flow"):
            pieces.append("完整流程等待确认" if result.get("requires_confirmation") else "完整流程已完成")
        if result.get("requires_confirmation"):
            pieces.append("需要人工确认后继续")
        if not pieces:
            pieces.append("需求已处理")
        return f"{task_label_for_intent(intent)}：{'，'.join(pieces)}。"


def task_label_for_intent(intent: str | None) -> str:
    labels = {
        "create_profile": "已生成简历",
        "update_profile": "已更新简历",
        "search_jobs": "已完成岗位推荐",
        "tailor_resume": "已完成简历定制",
        "quick_apply": "已生成投递材料",
        "interview_prep": "已生成面试准备",
        "full_flow": "已完成求职流程",
    }
    return labels.get(intent or "", "已处理需求")
