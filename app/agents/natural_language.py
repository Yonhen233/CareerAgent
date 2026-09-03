from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from contextvars import ContextVar
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agents.orchestrator import AgentOrchestrator
from app.agents.tools import bind_agent_tool
from app.core.config import get_settings
from app.core.llm import LLMCallBudget, LLMClient, llm_call_budget, llm_trace_context
from app.models.entities import AgentArtifact, AgentRun, Job, Profile
from app.models.schemas import (
    AgentRunRequest,
    GuidedProfileRequest,
    JobDiscoveryRequest,
    NaturalLanguageAgentRequest,
    TaskState,
)
from app.services.job_discovery import JobDiscoveryService
from app.services.langgraph_checkpointer import LangGraphCheckpointerLifecycle
from app.services.agent_reliability import AgentTaskContractService, AgentTaskIncompleteError
from app.services.conversation_compactor import ConversationCompactor
from app.services.execution_provenance import ExecutionProvenanceService
from app.services.jd_parser import JDParserService
from app.services.memory_feedback import CareerMemoryService
from app.services.resume_parser import ResumeParserService
from app.services.text_splitter import ResumeTextSplitter
from app.services.task_state import TaskStateReducer, TaskStateValidationError
from app.services.trace_service import TraceService
from app.services.token_optimization import DynamicToolCatalog, NodeTokenBudgetRegistry
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

ACTIONS = {
    "create_profile",
    "search_jobs",
    "tailor_resume",
    "quick_apply",
    "interview_prep",
    "full_flow",
}

PLAN_TECH_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9+.#-]{1,}(?![A-Za-z0-9])")
PLAN_QUERY_STOPWORDS = {
    "and",
    "for",
    "from",
    "not",
    "now",
    "only",
    "or",
    "please",
    "the",
    "with",
}
_NATURAL_MEMORY_SCOPE: ContextVar[dict[str, str | None]] = ContextVar(
    "natural_memory_scope",
    default={"tenant_id": None, "user_id": None},
)


class NaturalLanguageGraphState(TypedDict, total=False):
    request: dict[str, Any]
    run_id: int
    graph_thread_id: str
    plan: dict[str, Any]
    task_contract: dict[str, Any]
    context_refs: dict[str, Any]
    task_state: dict[str, Any]
    memory_context: dict[str, Any]
    completion_verification: dict[str, Any]
    result: dict[str, Any]
    execution_error: str | None
    error_envelope: dict[str, Any]
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
        self.job_discovery = JobDiscoveryService()
        self.settings = get_settings()
        self.task_contracts = AgentTaskContractService()
        self.dynamic_tool_catalog = DynamicToolCatalog()
        self.conversation_compactor = ConversationCompactor(self.llm)
        self.task_state_reducer = TaskStateReducer()
        self._runtime_dbs: dict[int, Session] = {}
        self._checkpoint_lifecycle = LangGraphCheckpointerLifecycle(settings=self.settings)
        self.checkpointer = None
        self._graph = None

    async def run(
        self,
        db: Session,
        request: NaturalLanguageAgentRequest,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        graph_thread_id = f"natural-run-{uuid4().hex}"
        run = self.trace.create_run(
            db,
            task_type="natural_language_request",
            profile_id=request.profile_id,
            job_id=request.job_id,
            tenant_id=tenant_id,
            user_id=user_id,
            input_json={
                **request.model_dump(),
                "orchestration_framework": "langgraph",
                "graph_thread_id": graph_thread_id,
            },
        )
        memory_scope_token = _NATURAL_MEMORY_SCOPE.set(
            {"tenant_id": tenant_id, "user_id": user_id}
        )
        self._runtime_dbs[run.id] = db
        self.trace.add_event(
            db,
            run_id=run.id,
            event_type="run_started",
            payload={"task_type": "natural_language_request", "graph_thread_id": graph_thread_id},
        )
        llm_budget = LLMCallBudget(
            name=f"natural_language_run:{run.id}",
            max_calls=self.settings.natural_agent_max_llm_calls,
            max_prompt_chars=self.settings.natural_agent_max_prompt_chars,
            max_completion_tokens=self.settings.natural_agent_max_completion_tokens,
            max_business_calls=(
                self.settings.llm_max_calls_per_run
                if self.settings.token_optimization_v2_enabled
                else None
            ),
            max_http_attempts=(
                self.settings.llm_max_attempts_per_run
                if self.settings.token_optimization_v2_enabled
                else None
            ),
            max_repair_calls=(
                self.settings.llm_max_repair_calls
                if self.settings.token_optimization_v2_enabled
                else None
            ),
            max_input_tokens=(
                self.settings.llm_max_input_tokens_per_run
                if self.settings.token_optimization_v2_enabled
                else None
            ),
            max_output_tokens=(
                self.settings.llm_max_output_tokens_per_run
                if self.settings.token_optimization_v2_enabled
                else None
            ),
            max_total_tokens=(
                self.settings.llm_max_total_tokens_per_run
                if self.settings.token_optimization_v2_enabled
                else None
            ),
        )
        try:
            graph = await self._ensure_graph()
            with llm_trace_context(
                workflow="natural_language_agent",
                workflow_run_id=str(run.id),
                agent_run_id=run.id,
                run_id=run.id,
            ), llm_call_budget(llm_budget):
                final_state = await self._invoke_graph(
                    graph,
                    {
                        "request": request.model_dump(),
                        "run_id": run.id,
                        "graph_thread_id": graph_thread_id,
                        "repair_attempts": [],
                        "task_state": (request.task_state or TaskState()).model_dump(),
                        "context_refs": {
                            "profile_id": request.profile_id,
                            "job_id": request.job_id,
                            "resume_version_id": request.resume_version_id,
                            "evidence_citations": [],
                            "artifact_ids": [],
                            "approval_id": None,
                            "tool_receipt_ids": [],
                            "conversation_summary_artifact_id": None,
                            "task_state_version": (request.task_state or TaskState()).version,
                            "data_versions": {},
                        },
                    },
                    db=db,
                    run_id=run.id,
                    config={"configurable": {"thread_id": graph_thread_id}},
                )
            self.trace.add_artifact(
                db,
                run_id=run.id,
                artifact_type="llm_budget",
                payload=llm_budget.to_dict(),
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
            self.trace.add_artifact(
                db,
                run_id=run.id,
                artifact_type="llm_budget",
                payload=llm_budget.to_dict(),
            )
            payload = {
                "run_id": run.id,
                "status": "failed",
                "user_message": f"处理失败：{self._public_error_message(exc)}",
                "plan_json": {},
                "result_json": {"error": str(exc)},
                "repair_attempts": [],
                "task_state": (request.task_state or TaskState()).model_dump(),
                "orchestration_framework": "langgraph",
                "graph_thread_id": graph_thread_id,
            }
            return self.trace.finish_run(
                db,
                run=run,
                status="failed",
                output_json=payload,
                error_message=str(exc),
                error_exception=exc,
                started_at=started,
            )
        finally:
            self._runtime_dbs.pop(run.id, None)
            _NATURAL_MEMORY_SCOPE.reset(memory_scope_token)
            await self._close_checkpoint()

    def _build_graph(self):
        graph = StateGraph(NaturalLanguageGraphState)
        graph.add_node("parse_user_request", self._node_parse_user_request)
        graph.add_node("execute_user_plan", self._node_execute_user_plan)
        graph.add_node("verify_user_plan", self._node_verify_user_plan)
        graph.add_node("repair_user_plan", self._node_repair_user_plan)
        graph.add_node("execute_repaired_user_plan", self._node_execute_repaired_user_plan)
        graph.add_node("verify_repaired_user_plan", self._node_verify_repaired_user_plan)
        graph.add_node("finalize_success", self._node_finalize_success)
        graph.add_node("finalize_failed", self._node_finalize_failed)
        graph.add_edge(START, "parse_user_request")
        graph.add_edge("parse_user_request", "execute_user_plan")
        graph.add_conditional_edges(
            "execute_user_plan",
            self._route_after_execute,
            {
                "repair_user_plan": "repair_user_plan",
                "verify_user_plan": "verify_user_plan",
                "finalize_failed": "finalize_failed",
            },
        )
        graph.add_conditional_edges(
            "verify_user_plan",
            self._route_after_verification,
            {"repair_user_plan": "repair_user_plan", "finalize_success": "finalize_success"},
        )
        graph.add_edge("repair_user_plan", "execute_repaired_user_plan")
        graph.add_conditional_edges(
            "execute_repaired_user_plan",
            self._route_after_repaired_execute,
            {"verify_repaired_user_plan": "verify_repaired_user_plan", "finalize_failed": "finalize_failed"},
        )
        graph.add_conditional_edges(
            "verify_repaired_user_plan",
            self._route_after_repaired_verification,
            {"finalize_success": "finalize_success", "finalize_failed": "finalize_failed"},
        )
        graph.add_edge("finalize_success", END)
        graph.add_edge("finalize_failed", END)
        return graph.compile(checkpointer=self.checkpointer)

    async def _ensure_graph(self):
        if self._graph is not None:
            return self._graph
        self.checkpointer = await self._checkpoint_lifecycle.open()
        self._graph = self._build_graph()
        return self._graph

    async def _close_checkpoint(self) -> None:
        await self._checkpoint_lifecycle.close()
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
            envelope = self.trace.error_classifier.classify(exc, step_name="LangGraph").as_dict()
            self.trace.add_event(
                db,
                run_id=run_id,
                event_type="graph_failed",
                payload={
                    "error": envelope["message"],
                    "error_type": exc.__class__.__name__,
                    "error_envelope": envelope,
                },
            )
            raise

    async def _node_parse_user_request(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        request = NaturalLanguageAgentRequest(**state["request"])
        db = self._db_from_state(state)
        plan = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="parse_user_request",
            input_json={"instruction": request.instruction},
            tool=bind_agent_tool(
                "llm.intent_planner",
                lambda: self._build_plan_for_run(db, request, run_id=state["run_id"]),
            ),
        )
        previous_task_state = TaskState.model_validate(
            state.get("task_state") or request.task_state or {}
        )
        source_message_id = self._current_user_message_id(request, run_id=state["run_id"])
        task_state = self.task_state_reducer.merge(
            previous_task_state,
            plan.get("state_updates"),
            source_message_id=source_message_id,
            source_role="user",
            source_text=request.instruction,
        )
        plan["task_state_transition"] = {
            "source_message_id": source_message_id,
            "previous_version": previous_task_state.version,
            "current_version": task_state.version,
        }
        run = db.query(AgentRun).filter(AgentRun.id == state["run_id"]).one()
        memory_context = CareerMemoryService().compact_context(
            db,
            tenant_id=run.tenant_id,
            user_id=run.user_id,
            profile_id=request.profile_id,
        )
        provenance = ExecutionProvenanceService().build(
            task_type="natural_language_request",
            plan={
                "steps": [
                    {"tool": "llm.intent_planner"},
                    {"tool": "NaturalLanguageAgentService"},
                ]
            },
        )
        plan["memory_context"] = {
            "version": memory_context["version"],
            "item_count": memory_context["item_count"],
            "context_chars": memory_context["context_chars"],
        }
        plan["execution_provenance"] = provenance
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="natural_language_plan", payload=plan)
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="task_state_transition",
            payload={
                "source_message_id": source_message_id,
                "state_updates": plan.get("state_updates") or {},
                "previous_state": previous_task_state.model_dump(),
                "current_state": task_state.model_dump(),
            },
        )
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="memory_context",
            payload=memory_context,
        )
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="execution_provenance",
            payload=provenance,
        )
        contract = {
            "version": "careeragent-natural-task-contract-v1",
            "intent": plan.get("intent"),
            "required_actions": list(plan.get("actions") or []),
            "completion_rule": "Every requested action must have an outcome or a human interrupt before success.",
            "repair_budget": 1,
        }
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="task_contract",
            payload=contract,
        )
        context_refs = dict(state.get("context_refs") or {})
        summary_artifact_id = (plan.get("context_management") or {}).get(
            "conversation_summary_artifact_id"
        )
        summary_state_version = int(
            (plan.get("context_management") or {}).get("conversation_summary_task_state_version")
            or 0
        )
        context_refs["task_state_version"] = task_state.version
        if summary_artifact_id and summary_state_version == task_state.version:
            context_refs["conversation_summary_artifact_id"] = summary_artifact_id
            context_refs["artifact_ids"] = list(
                dict.fromkeys([*(context_refs.get("artifact_ids") or []), summary_artifact_id])
            )
        elif summary_artifact_id:
            context_refs["conversation_summary_artifact_id"] = None
        return {
            "plan": plan,
            "task_contract": contract,
            "memory_context": memory_context,
            "context_refs": context_refs,
            "task_state": task_state.model_dump(),
        }

    async def _node_execute_user_plan(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        request = self._request_with_task_state(
            NaturalLanguageAgentRequest(**state["request"]), state.get("task_state")
        )
        db = self._db_from_state(state)
        plan = state.get("plan") or {}
        try:
            result = await self.trace.step(
                db,
                run_id=state["run_id"],
                step_name="execute_user_plan",
                input_json={"intent": plan.get("intent")},
                tool=bind_agent_tool(
                    "NaturalLanguageAgentService",
                    lambda: self._execute_plan(db, request, plan),
                ),
            )
            return {"result": result, "execution_error": None}
        except Exception as exc:  # noqa: BLE001
            envelope = self.trace.error_classifier.classify(
                exc,
                tool_name="NaturalLanguageAgentService",
                step_name="execute_user_plan",
            ).as_dict()
            self.trace.add_artifact(
                db,
                run_id=state["run_id"],
                artifact_type="error_envelope",
                payload=envelope,
            )
            return {"execution_error": str(exc), "error_envelope": envelope}

    async def _node_repair_user_plan(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        request = NaturalLanguageAgentRequest(**state["request"])
        db = self._db_from_state(state)
        plan = state.get("plan") or {}
        error = RuntimeError(state.get("execution_error") or "执行失败")
        repaired_plan = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="repair_user_plan",
            input_json={"error": str(error), "plan": plan},
            tool=bind_agent_tool(
                "llm.intent_planner",
                lambda: self._repair_plan(
                    db,
                    request,
                    plan,
                    error,
                    completed_result=state.get("result") or {},
                ),
            ),
        )
        repair_attempts = [
            *(state.get("repair_attempts") or []),
            {
                "error": str(error),
                "error_envelope": state.get("error_envelope") or {},
                "repaired_intent": repaired_plan.get("intent"),
            },
        ]
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="natural_language_repaired_plan",
            payload=repaired_plan,
        )
        return {
            "plan": repaired_plan,
            "repair_attempts": repair_attempts,
            "execution_error": None,
            "error_envelope": {},
        }

    async def _node_execute_repaired_user_plan(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        request = self._request_with_task_state(
            NaturalLanguageAgentRequest(**state["request"]), state.get("task_state")
        )
        db = self._db_from_state(state)
        plan = state.get("plan") or {}
        try:
            result = await self.trace.step(
                db,
                run_id=state["run_id"],
                step_name="execute_repaired_user_plan",
                input_json={"intent": plan.get("intent")},
                tool=bind_agent_tool(
                    "NaturalLanguageAgentService",
                    lambda: self._execute_plan(
                        db,
                        request,
                        plan,
                        prior_result=state.get("result") or {},
                    ),
                ),
            )
            return {"result": result, "execution_error": None}
        except Exception as exc:  # noqa: BLE001
            envelope = self.trace.error_classifier.classify(
                exc,
                tool_name="NaturalLanguageAgentService",
                step_name="execute_repaired_user_plan",
            ).as_dict()
            self.trace.add_artifact(
                db,
                run_id=state["run_id"],
                artifact_type="error_envelope",
                payload=envelope,
            )
            return {
                "execution_error": str(exc),
                "error_envelope": envelope,
                "result": {"error": str(exc), "error_envelope": envelope},
            }

    async def _node_verify_user_plan(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        return self._verify_natural_language_completion(state, repaired=False)

    async def _node_verify_repaired_user_plan(self, state: NaturalLanguageGraphState) -> dict[str, Any]:
        return self._verify_natural_language_completion(state, repaired=True)

    def _verify_natural_language_completion(
        self,
        state: NaturalLanguageGraphState,
        *,
        repaired: bool,
    ) -> dict[str, Any]:
        report = self.task_contracts.verify_natural_language(
            plan=state.get("plan") or {},
            result=state.get("result") or {},
            request=state.get("request") or {},
        )
        report["after_repair"] = repaired
        db = self._db_from_state(state)
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="natural_language_completion_verification",
            payload=report,
        )
        self.trace.add_event(
            db,
            run_id=state["run_id"],
            event_type="completion_gate_passed" if report["passed"] else "completion_gate_rejected",
            node_name="verify_repaired_user_plan" if repaired else "verify_user_plan",
            payload=report,
        )
        if report["passed"]:
            return {"completion_verification": report, "execution_error": None}
        error = (
            f"任务未完成，缺少动作结果：{', '.join(report['missing_actions'])}；"
            f"结果一致性问题：{json.dumps(report.get('integrity_violations') or [], ensure_ascii=False)}"
        )
        envelope = self.trace.error_classifier.classify(
            AgentTaskIncompleteError(error),
            tool_name="completion_gate",
            step_name="verify_repaired_user_plan" if repaired else "verify_user_plan",
        ).as_dict()
        return {
            "completion_verification": report,
            "execution_error": error,
            "error_envelope": envelope,
        }

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
            "completion_verification": state.get("completion_verification") or {},
            "task_state": state.get("task_state") or {},
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
            "error_envelope": state.get("error_envelope") or {},
            "repair_attempts": state.get("repair_attempts") or [],
            "task_state": state.get("task_state") or {},
        }
        return {"output": payload}

    def _route_after_execute(self, state: NaturalLanguageGraphState) -> str:
        if not state.get("execution_error"):
            return "verify_user_plan"
        category = str((state.get("error_envelope") or {}).get("category") or "")
        if category in {"input_or_state_validation", "completion_gate_rejected"}:
            return "repair_user_plan"
        return "finalize_failed"

    def _route_after_verification(self, state: NaturalLanguageGraphState) -> str:
        return "repair_user_plan" if state.get("execution_error") else "finalize_success"

    def _route_after_repaired_execute(self, state: NaturalLanguageGraphState) -> str:
        return "finalize_failed" if state.get("execution_error") else "verify_repaired_user_plan"

    def _route_after_repaired_verification(self, state: NaturalLanguageGraphState) -> str:
        return "finalize_failed" if state.get("execution_error") else "finalize_success"

    def _db_from_state(self, state: NaturalLanguageGraphState) -> Session:
        db = self._runtime_dbs.get(int(state["run_id"]))
        if db is None:
            raise RuntimeError("Natural language LangGraph state is missing the active database session.")
        return db

    @staticmethod
    def _current_user_message_id(
        request: NaturalLanguageAgentRequest,
        *,
        run_id: int,
    ) -> str:
        if request.message_id and request.message_id.strip():
            return request.message_id.strip()
        for message in reversed(request.conversation_messages):
            if str(message.get("role") or "") != "user":
                continue
            if str(message.get("content") or "").strip() == request.instruction.strip():
                value = str(message.get("message_id") or "").strip()
                if value:
                    return value
        return f"run-{run_id}:user"

    @staticmethod
    def _request_with_task_state(
        request: NaturalLanguageAgentRequest,
        task_state: dict[str, Any] | None,
    ) -> NaturalLanguageAgentRequest:
        current = TaskState.model_validate(task_state or request.task_state or {})
        updates: dict[str, Any] = {"task_state": current}
        if current.location:
            updates["location"] = current.location
        if current.target_role and request.query in {None, "", "Agent 开发实习生"}:
            updates["query"] = current.target_role
        return request.model_copy(update=updates)

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

    async def _build_plan(
        self,
        db: Session,
        request: NaturalLanguageAgentRequest,
        *,
        run_id: int | None = None,
    ) -> dict[str, Any]:
        profile = self._resolve_profile(db, request.profile_id)
        memory_scope = _NATURAL_MEMORY_SCOPE.get()
        memory_context = CareerMemoryService().compact_context(
            db,
            tenant_id=memory_scope.get("tenant_id") or (profile.tenant_id if profile else None),
            user_id=memory_scope.get("user_id"),
            profile_id=request.profile_id,
        )
        system_prompt = (
            "你是中文求职助手 Agent 的意图规划器。只返回 JSON。"
            "不要编造用户没有提供的经历；缺少必要 ID 时优先使用用户文本生成 profile 或 job。"
            "你必须在同一次调用中返回执行计划和 state_updates。当前任务状态是执行依据；"
            "对话摘要只是非权威背景。未提到的状态字段不要输出，不能用空值覆盖旧状态。"
        )
        planner_profile_context = request.profile_context or {}
        if self.settings.token_optimization_v2_enabled:
            planner_profile_context = self._compact_planner_profile(planner_profile_context)
        tool_catalog = self.dynamic_tool_catalog.select(
            task_type="full_career_flow",
            node="planner",
            max_risk="low",
            include_full_schema=False,
        )
        conversation = await self.conversation_compactor.compact_if_needed(
            db,
            run_id=run_id,
            messages=(
                request.conversation_messages
                if self.settings.context_management_v3_enabled
                else []
            ),
            node_budget_tokens=NodeTokenBudgetRegistry().get("planner").max_input_tokens,
            task_state=request.task_state,
        )
        conversation_context = {
            "summary": conversation.summary,
            "recent_messages": conversation.recent_messages,
        }
        user_prompt = f"""
根据用户需求生成可执行计划。

可选 intent:
- create_profile: 根据用户自然语言生成简历档案
- update_profile: 修改已有简历档案并生成新档案
- search_jobs: 按求职偏好浏览岗位；简历可选，有简历时增加匹配分析
- tailor_resume: 根据岗位/JD 定制简历
- quick_apply: 生成投递包
- interview_prep: 生成面试准备包
- full_flow: 建档/找岗/定制/投递包/面试包完整流程

重要约束:
- 如果用户明确说“不要投递 / 不投递 / 不要申请 / 只改简历 / 只生成面试准备”，不要选择 quick_apply 或 full_flow。
- 如果用户同时要求“改简历”和“面试准备”，intent 可选 interview_prep，并在 actions 中同时写入 tailor_resume 与 interview_prep。
- full_flow 只用于用户明确要求一键完整流程或包含投递材料。
- actions 用于表达 intent 之外还要串联执行的步骤；intent 自身对应的动作可以同时写入 actions。
- “搜索后再定制”可使用 search_jobs 作为主 intent，并在 actions 中写入 search_jobs、tailor_resume。
- 多动作任务的 intent 必须表示最后一个主要用户结果。例如“先建档再搜索”使用 search_jobs，actions 写 create_profile、search_jobs。
- query 必须保留用户明确给出的目标岗位和正向技术偏好，不能只把 RAG、tool calling 等偏好写在 reason。
- update_profile 的每一项修改内容都必须写入 profile；项目事实放进 projects/work_experience，不能只在 reason 中复述。
- quick_apply 表示生成待审批投递材料，不等于直接外发；用户明确要求准备投递材料时应选择它。

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
  "state_updates": {{
    "goal": {{"operation": "set|clear", "value": string|null}}|null,
    "target_role": {{"operation": "set|clear", "value": string|null}}|null,
    "location": {{"operation": "set|clear", "value": string|null}}|null,
    "constraints_to_add": [string],
    "constraints_to_remove": [string],
    "forbidden_actions_to_add": ["auto_apply|browser_apply|email_send|cross_tenant_data_access|external_send|unapproved_high_risk_action"],
    "forbidden_actions_to_remove": [string],
    "selected_actions_to_add": [string],
    "selected_actions_to_remove": [string],
    "pending_actions_to_add": [string],
    "pending_actions_to_remove": [string],
    "completed_actions_to_add": [string]
  }},
  "reason": string
}}

上下文:
profile_id={request.profile_id}
job_id={request.job_id}
resume_version_id={request.resume_version_id}
query={request.query}
location={request.location}
jd_text={request.jd_text or ""}
selected_actions={request.selected_actions}
profile_context={json.dumps(planner_profile_context, ensure_ascii=False)}
typed_memory={json.dumps(memory_context, ensure_ascii=False)}
conversation={json.dumps(conversation_context, ensure_ascii=False)}
current_task_state={json.dumps((request.task_state or TaskState()).model_dump(), ensure_ascii=False)}
available_tool_catalog={json.dumps(tool_catalog.compact_catalog, ensure_ascii=False)}

用户需求:
{request.instruction}
"""
        with llm_trace_context(stage="natural_language_plan", agent_run_task="natural_language_request"):
            call_kwargs = dict(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.05,
                max_tokens=1200,
                db=db,
                trace_name="natural_language.plan",
            )
            if isinstance(self.llm, LLMClient):
                call_kwargs["prompt_sections"] = {
                    "task_contract": user_prompt[: user_prompt.index("上下文:")],
                    "profile": planner_profile_context,
                    "job": {
                        "job_id": request.job_id,
                        "jd_text": request.jd_text or "",
                    },
                    "memory": memory_context,
                    "conversation_history": conversation_context,
                    "tool_schemas": tool_catalog.compact_catalog,
                    "working": {
                        "instruction": request.instruction,
                        "query": request.query,
                        "location": request.location,
                        "selected_actions": request.selected_actions,
                        "task_state": (request.task_state or TaskState()).model_dump(),
                    },
                }
            plan = await self.llm.generate_json(**call_kwargs)
        normalized = self._normalize_plan(plan, request)
        contract_errors = self._plan_contract_errors(normalized, request)
        if not contract_errors:
            normalized["context_management"] = {
                "conversation_compactor_called": conversation.compactor_called,
                "conversation_summary_artifact_id": conversation.summary_artifact_id,
                "conversation_original_tokens": conversation.original_tokens,
                "conversation_final_tokens": conversation.final_tokens,
                "recent_message_count": len(conversation.recent_messages),
                "conversation_compactor_attempts": conversation.compactor_attempts,
                "conversation_fallback_to_raw": conversation.fallback_to_raw,
                "conversation_validation_errors": conversation.validation_errors,
                "conversation_summary_task_state_version": (
                    (conversation.summary or {}).get("task_state_version")
                ),
            }
            return normalized
        repaired = await self._repair_plan_contract(db, request, normalized, contract_errors)
        repaired["contract_repairs"] = contract_errors
        remaining_errors = self._plan_contract_errors(repaired, request)
        if remaining_errors:
            raise ValueError(f"计划契约校验失败：{'；'.join(remaining_errors)}")
        repaired["context_management"] = {
            "conversation_compactor_called": conversation.compactor_called,
            "conversation_summary_artifact_id": conversation.summary_artifact_id,
            "conversation_original_tokens": conversation.original_tokens,
            "conversation_final_tokens": conversation.final_tokens,
            "recent_message_count": len(conversation.recent_messages),
            "conversation_compactor_attempts": conversation.compactor_attempts,
            "conversation_fallback_to_raw": conversation.fallback_to_raw,
            "conversation_validation_errors": conversation.validation_errors,
            "conversation_summary_task_state_version": (
                (conversation.summary or {}).get("task_state_version")
            ),
        }
        return repaired

    async def _build_plan_for_run(
        self,
        db: Session,
        request: NaturalLanguageAgentRequest,
        *,
        run_id: int,
    ) -> dict[str, Any]:
        parameters = inspect.signature(self._build_plan).parameters
        if "run_id" in parameters:
            return await self._build_plan(db, request, run_id=run_id)
        return await self._build_plan(db, request)

    async def _repair_plan_contract(
        self,
        db: Session,
        request: NaturalLanguageAgentRequest,
        plan: dict[str, Any],
        errors: list[str],
    ) -> dict[str, Any]:
        base_profile = self._resolve_profile(db, request.profile_id)
        base_profile_json = dict(base_profile.structured_profile_json or {}) if base_profile else {}
        system_prompt = (
            "你是 Agent 计划契约修复器。只返回完整 JSON 计划。"
            "只补齐用户明确要求但计划遗漏的字段，不增加用户没有提供的经历或动作。"
            "保留合法 state_updates；未知字段、未知 Action 和未经用户明确解除的禁止操作不得输出。"
        )
        user_prompt = f"""
当前计划未通过执行前契约校验：
{json.dumps(errors, ensure_ascii=False)}

原计划：
{json.dumps(plan, ensure_ascii=False)}

用户原始需求：
{request.instruction}

当前正式任务状态：
{json.dumps((request.task_state or TaskState()).model_dump(), ensure_ascii=False)}

现有简历档案（只用于定位应更新的条目）：
{json.dumps(base_profile_json, ensure_ascii=False)}

修复要求：
- 保留原计划已正确的字段和安全边界。
- update_profile 的技能更新写入 skills；项目/职责事实写入 projects 或 work_experience，不能只写在 reason。
- 多动作任务的 intent 表示最后一个主要用户结果，actions 保留完整执行顺序。
- 返回与原计划完全相同的 JSON schema，不要添加解释文本。
"""
        with llm_trace_context(stage="natural_language_plan_contract_repair", agent_run_task="natural_language_request"):
            repaired = await self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0,
                max_tokens=1400,
                db=db,
                trace_name="natural_language.repair_plan_contract",
            )
        return self._normalize_plan(repaired, request)

    async def _repair_plan(
        self,
        db: Session,
        request: NaturalLanguageAgentRequest,
        plan: dict[str, Any],
        error: Exception,
        *,
        completed_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system_prompt = "你是 Agent 计划修复器。只返回 JSON；不得绕过事实校验、投递门禁或人工确认边界。"
        user_prompt = f"""
原计划执行失败，请基于错误修复一次计划。

错误:
{error}

原计划:
{json.dumps(plan, ensure_ascii=False)}

已经完成且不得重复执行的结果:
{json.dumps(self._completed_result_summary(completed_result or {}), ensure_ascii=False)}

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
修复计划可以保留完整目标，但执行器只会补齐缺失结果，不得要求重复外发或重复创建已有产物。
返回与原计划相同 JSON schema。
"""
        with llm_trace_context(stage="natural_language_repair", agent_run_task="natural_language_request"):
            repaired = await self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.05,
                max_tokens=1200,
                db=db,
                trace_name="natural_language.repair_plan",
            )
        return self._normalize_plan(repaired, request)

    async def _execute_plan(
        self,
        db: Session,
        request: NaturalLanguageAgentRequest,
        plan: dict[str, Any],
        *,
        prior_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        intent = plan["intent"]
        prior = dict(prior_result or {})
        prior_profile_id = (prior.get("profile") or {}).get("id")
        prior_job_id = (prior.get("job") or {}).get("id")
        profile = self._resolve_profile(db, request.profile_id or prior_profile_id)
        if intent == "update_profile":
            profile = self._create_profile_from_plan(db, plan, request=request, base_profile=profile)
        elif profile is None and (plan.get("profile") or request.profile_context):
            profile = self._create_profile_from_plan(db, plan, request=request, base_profile=None)

        job = self._resolve_job(db, request.job_id or prior_job_id)
        if job is None and (request.jd_text or (plan.get("job") or {}).get("jd_text")):
            job = await self._create_job_from_plan(db, request, plan)

        result: dict[str, Any] = {
            **prior,
            "profile": self._profile_payload(profile),
            "job": self._job_payload(job),
            "agent_runs": list(prior.get("agent_runs") or []),
        }

        selected_actions = {self._canonical_action(action) for action in (plan.get("actions") or [])}
        selected_actions.discard("")
        implicit_action = {
            "create_profile": "create_profile",
            "update_profile": "create_profile",
            "search_jobs": "search_jobs",
            "tailor_resume": "tailor_resume",
            "quick_apply": "quick_apply",
            "interview_prep": "interview_prep",
            "full_flow": "full_flow",
        }.get(intent)
        effective_actions = set(selected_actions)
        if implicit_action:
            effective_actions.add(implicit_action)

        if intent in {"create_profile", "update_profile"} and effective_actions <= {"create_profile"}:
            if profile is None:
                raise ValueError("需要简历信息才能生成简历档案。")
            return result

        downstream_actions = {"tailor_resume", "quick_apply", "interview_prep"}
        if "search_jobs" in effective_actions and intent != "full_flow":
            if result.get("matches"):
                pass
            elif profile is not None:
                run = await self._run_orchestrator(
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
            else:
                discovery = await self.job_discovery.discover(
                    db,
                    JobDiscoveryRequest(
                        preference_text=plan.get("query") or request.query,
                        profile_id=None,
                        location=request.location,
                        limit=request.limit,
                        source_mode="hybrid",
                    ),
                )
                result["job_search_session_id"] = discovery.id
                result["matches"] = [
                    {
                        "job_id": item.job_id,
                        "match_result_id": item.match_result_id,
                        "rank": item.rank,
                        "retrieval_score": item.retrieval_score,
                        "match_score": item.match_score,
                        "final_score": item.final_score,
                        "title": item.job.title,
                        "company": item.job.company,
                        "location": item.job.location,
                        "reason": item.reason_json or {},
                    }
                    for item in discovery.results
                ]
                if not result["matches"]:
                    raise ValueError("岗位搜索没有返回结果，请调整求职偏好或岗位来源。")
            if not effective_actions.intersection(downstream_actions):
                return result
            profile = self._require_profile(profile)
            if job is None:
                job = self._resolve_job(db, int(result["matches"][0]["job_id"]))
                result["job"] = self._job_payload(job)

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
            run = await self._run_orchestrator(
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
            if not (result.get("tailor") or {}).get("resume_version_id"):
                tailor_run = await self._run_orchestrator(
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
        if wants_interview:
            if not (result.get("interview_prep") or {}).get("interview_prep_id"):
                interview_run = await self._run_orchestrator(
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
        if intent == "quick_apply" or "quick_apply" in selected_actions:
            if not (result.get("application") or {}).get("application_id") and not result.get("requires_confirmation"):
                apply_run = await self._run_orchestrator(
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
        return result

    async def _run_orchestrator(self, db: Session, request: AgentRunRequest):
        scope = _NATURAL_MEMORY_SCOPE.get()
        parameters = inspect.signature(self.orchestrator.run).parameters
        kwargs: dict[str, Any] = {}
        if "tenant_id" in parameters:
            kwargs["tenant_id"] = scope.get("tenant_id")
        if "user_id" in parameters:
            kwargs["user_id"] = scope.get("user_id")
        return await self.orchestrator.run(db, request, **kwargs)

    def _completed_result_summary(self, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "profile_id": (result.get("profile") or {}).get("id"),
            "job_id": (result.get("job") or {}).get("id"),
            "match_count": len(result.get("matches") or []),
            "resume_version_id": (result.get("tailor") or {}).get("resume_version_id"),
            "application_id": (result.get("application") or {}).get("application_id"),
            "interview_prep_id": (result.get("interview_prep") or {}).get("interview_prep_id"),
            "waiting_for_confirmation": bool(result.get("requires_confirmation")),
        }

    def _normalize_plan(self, plan: dict[str, Any], request: NaturalLanguageAgentRequest) -> dict[str, Any]:
        intent = str(plan.get("intent") or "").strip()
        if intent not in INTENTS:
            intent = self._heuristic_intent(request.instruction)
        if self._forbids_application(request.instruction) and intent in {"quick_apply", "full_flow"}:
            intent = "interview_prep" if self._text_wants_interview(request.instruction) else "tailor_resume"
        selected_actions = [self._canonical_action(action) for action in request.selected_actions]
        selected_actions = [action for action in selected_actions if action]
        raw_actions = [item for item in plan.get("actions", []) if str(item).strip()]
        canonical_actions = [self._canonical_action(item) for item in raw_actions]
        unknown_actions = [str(item) for item, canonical in zip(raw_actions, canonical_actions) if not canonical]
        normalized = {
            "intent": intent,
            "query": plan.get("query") or request.query or "Agent 开发实习生",
            "profile": plan.get("profile") if isinstance(plan.get("profile"), dict) else None,
            "job": plan.get("job") if isinstance(plan.get("job"), dict) else None,
            "needs_profile": bool(plan.get("needs_profile", intent != "create_profile")),
            "needs_job": bool(plan.get("needs_job", intent in {"tailor_resume", "quick_apply", "interview_prep"})),
            "actions": canonical_actions,
            "plan_action_errors": (
                ["计划包含未知 Action：" + ", ".join(unknown_actions)] if unknown_actions else []
            ),
            "reason": str(plan.get("reason") or ""),
        }
        try:
            normalized["state_updates"] = self.task_state_reducer.validate_updates(
                plan.get("state_updates")
            ).model_dump(exclude_none=True)
            normalized["state_update_errors"] = []
        except TaskStateValidationError as exc:
            normalized["state_updates"] = {}
            normalized["state_update_errors"] = [str(exc)]
        normalized["actions"] = [action for action in normalized["actions"] if action]
        if selected_actions:
            normalized["actions"] = list(dict.fromkeys(selected_actions))
            normalized["reason"] = (normalized["reason"] + " 显式生成项来自用户勾选。").strip()
            if "quick_apply" in selected_actions:
                intent = "quick_apply"
            elif "interview_prep" in selected_actions:
                intent = "interview_prep"
            elif "tailor_resume" in selected_actions:
                intent = "tailor_resume"
            elif "search_jobs" in selected_actions:
                intent = "search_jobs"
            else:
                intent = "create_profile"
            normalized["intent"] = intent
            normalized["needs_profile"] = any(
                action in selected_actions for action in ["search_jobs", "tailor_resume", "quick_apply", "interview_prep"]
            ) or "create_profile" in selected_actions
            normalized["needs_job"] = any(
                action in selected_actions for action in ["tailor_resume", "quick_apply", "interview_prep"]
            )
        if self._forbids_application(request.instruction):
            normalized["actions"] = [
                action
                for action in normalized["actions"]
                if action not in {"quick_apply", "full_flow", "application_packet", "apply", "submit_application"}
            ]
            if normalized["intent"] in {"quick_apply", "full_flow"}:
                normalized["intent"] = "interview_prep" if self._text_wants_interview(request.instruction) else "tailor_resume"
            if self._text_wants_tailor(request.instruction) and "tailor_resume" not in normalized["actions"]:
                normalized["actions"].append("tailor_resume")
            if self._text_wants_interview(request.instruction) and "interview_prep" not in normalized["actions"]:
                normalized["actions"].append("interview_prep")
        if self._forbids_tailor(request.instruction):
            normalized["actions"] = [
                action for action in normalized["actions"] if action != "tailor_resume"
            ]
            if normalized["intent"] == "tailor_resume":
                normalized["intent"] = "create_profile"
                normalized["needs_job"] = False
        normalized["actions"] = list(dict.fromkeys(normalized["actions"]))
        normalized["intent"] = self._terminal_intent(normalized["intent"], normalized["actions"])
        if normalized["intent"] == "search_jobs" or "search_jobs" in normalized["actions"]:
            normalized["query"] = self._preserve_instruction_terms_in_query(
                str(normalized.get("query") or ""),
                request.instruction,
            )
        if request.jd_text:
            normalized["job"] = {**(normalized["job"] or {}), "jd_text": request.jd_text}
        normalized_actions = set(normalized["actions"])
        has_profile_input = bool(request.profile_id or request.profile_context or normalized.get("profile"))
        profile_dependent = bool(
            normalized_actions.intersection({"tailor_resume", "quick_apply", "interview_prep", "full_flow"})
            or normalized["intent"] in {"update_profile", "tailor_resume", "quick_apply", "interview_prep", "full_flow"}
            or (normalized["intent"] == "search_jobs" and has_profile_input)
        )
        job_dependent = bool(
            normalized_actions.intersection({"tailor_resume", "quick_apply", "interview_prep"})
            or normalized["intent"] in {"tailor_resume", "quick_apply", "interview_prep"}
        )
        normalized["needs_profile"] = profile_dependent
        normalized["needs_job"] = job_dependent
        return normalized

    @staticmethod
    def _compact_planner_profile(profile: dict[str, Any]) -> dict[str, Any]:
        compact = {
            key: profile.get(key)
            for key in ("id", "name", "headline", "target_roles", "skills")
            if profile.get(key) not in (None, "", [], {})
        }
        projects = []
        for item in profile.get("projects") or []:
            if not isinstance(item, dict):
                continue
            projects.append(
                {
                    key: item.get(key)
                    for key in ("name", "tech_stack", "impact")
                    if item.get(key) not in (None, "", [], {})
                }
            )
        if projects:
            compact["projects"] = projects[:5]
        work = []
        for item in profile.get("work_experience") or []:
            if not isinstance(item, dict):
                continue
            work.append(
                {
                    key: item.get(key)
                    for key in ("company", "role", "duration", "tech_stack")
                    if item.get(key) not in (None, "", [], {})
                }
            )
        if work:
            compact["work_experience"] = work[:5]
        return compact

    def _terminal_intent(self, intent: str, actions: list[str]) -> str:
        if intent == "full_flow" or "full_flow" in actions:
            return "full_flow"
        action_set = set(actions)
        for action in ("quick_apply", "interview_prep", "tailor_resume", "search_jobs"):
            if action in action_set:
                return action
        return intent

    def _preserve_instruction_terms_in_query(self, query: str, instruction: str) -> str:
        clean_query = " ".join(str(query or "").split())
        lowered_query = clean_query.lower()
        preserved: list[str] = []
        for match in PLAN_TECH_TOKEN_RE.finditer(instruction or ""):
            term = match.group(0)
            lowered = term.lower()
            if lowered in PLAN_QUERY_STOPWORDS or lowered in lowered_query:
                continue
            prefix = (instruction or "")[max(0, match.start() - 12) : match.start()].lower()
            if re.search(r"(?:不要|不需要|无需|排除|不考虑|避免|\bno\b|\bnot\b|\bwithout\b)\s*$", prefix):
                continue
            preserved.append(term)
            lowered_query += f" {lowered}"
        return " ".join([clean_query, *preserved]).strip()

    def _plan_contract_errors(
        self,
        plan: dict[str, Any],
        request: NaturalLanguageAgentRequest,
    ) -> list[str]:
        errors: list[str] = []
        errors.extend(str(item) for item in plan.get("state_update_errors") or [])
        errors.extend(str(item) for item in plan.get("plan_action_errors") or [])
        if not errors:
            try:
                self.task_state_reducer.merge(
                    request.task_state,
                    plan.get("state_updates"),
                    source_message_id=request.message_id or "planner-contract-validation",
                    source_role="user",
                    source_text=request.instruction,
                )
            except TaskStateValidationError as exc:
                errors.append(str(exc))
        if plan.get("intent") != "update_profile":
            return errors
        profile = plan.get("profile") if isinstance(plan.get("profile"), dict) else None
        if not profile:
            return ["update_profile 必须提供非空 profile patch"]
        profile_text = json.dumps(profile, ensure_ascii=False).lower()
        missing_terms = [
            term
            for term in self._instruction_technical_terms(request.instruction)
            if term.lower() not in profile_text
        ]
        if missing_terms:
            errors.append(f"profile patch 遗漏用户明确提供的技术/项目事实：{', '.join(missing_terms)}")
        return errors

    def _instruction_technical_terms(self, instruction: str) -> list[str]:
        terms: list[str] = []
        for match in PLAN_TECH_TOKEN_RE.finditer(instruction or ""):
            term = match.group(0)
            if term.lower() in PLAN_QUERY_STOPWORDS:
                continue
            terms.append(term)
        return list(dict.fromkeys(terms))

    def _canonical_action(self, action: Any) -> str:
        value = str(action or "").strip()
        mapping = {
            "create_profile": "create_profile",
            "profile": "create_profile",
            "resume_profile": "create_profile",
            "search_jobs": "search_jobs",
            "search_jobs_by_profile": "search_jobs",
            "find_jobs": "search_jobs",
            "tailor_resume": "tailor_resume",
            "resume_tailor": "tailor_resume",
            "quick_apply": "quick_apply",
            "application_packet": "quick_apply",
            "apply": "quick_apply",
            "interview_prep": "interview_prep",
            "prepare_interview": "interview_prep",
            "generate_interview_prep": "interview_prep",
            "full_flow": "full_flow",
        }
        return mapping.get(value, value if value in ACTIONS else "")

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
        if self._forbids_tailor(instruction):
            return False
        text = instruction.lower()
        return any(word in text for word in ["改简历", "修改简历", "优化简历", "定制简历", "tailor resume"])

    def _forbids_tailor(self, instruction: str) -> bool:
        text = instruction.lower()
        negative_markers = [
            "不要改简历",
            "不改简历",
            "无需改简历",
            "不用改简历",
            "不要修改简历",
            "不要优化简历",
            "不要定制简历",
            "不定制简历",
            "don't tailor",
            "do not tailor",
        ]
        return any(marker in text for marker in negative_markers)

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
        request: NaturalLanguageAgentRequest | None = None,
        base_profile: Profile | None,
    ) -> Profile:
        profile_data = dict(base_profile.structured_profile_json or {}) if base_profile else {}
        if request and request.profile_context:
            profile_data = self._merge_profile_patch(profile_data, request.profile_context)
        profile_data = self._merge_profile_patch(profile_data, plan.get("profile") or {})
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

    def _merge_profile_patch(self, base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        additive_fields = {
            "target_roles",
            "skills",
            "awards",
            "languages",
            "certifications",
            "portfolio_links",
            "enabled_sections",
        }
        keyed_fields = {
            "projects": ("name",),
            "work_experience": ("company", "role"),
            "education": ("school", "major", "degree"),
            "campus_experience": ("company", "role"),
        }
        for field, value in patch.items():
            if value is None:
                continue
            if field in additive_fields and isinstance(value, list):
                existing = list(merged.get(field) or [])
                merged[field] = list(dict.fromkeys([*existing, *value]))
                continue
            if field in keyed_fields and isinstance(value, list):
                merged[field] = self._merge_profile_items(
                    list(merged.get(field) or []),
                    value,
                    identity_fields=keyed_fields[field],
                )
                continue
            if value != "":
                merged[field] = value
        return merged

    def _merge_profile_items(
        self,
        existing: list[Any],
        updates: list[Any],
        *,
        identity_fields: tuple[str, ...],
    ) -> list[Any]:
        merged = [dict(item) if isinstance(item, dict) else item for item in existing]
        for raw_update in updates:
            if not isinstance(raw_update, dict):
                if raw_update not in merged:
                    merged.append(raw_update)
                continue
            update = dict(raw_update)
            identity = tuple(str(update.get(field) or "").strip().lower() for field in identity_fields)
            match_index = None
            if any(identity):
                for index, item in enumerate(merged):
                    if not isinstance(item, dict):
                        continue
                    candidate = tuple(str(item.get(field) or "").strip().lower() for field in identity_fields)
                    if candidate == identity:
                        match_index = index
                        break
            if match_index is None:
                merged.append(update)
                continue
            current = dict(merged[match_index])
            for key, value in update.items():
                if isinstance(value, list):
                    current[key] = list(dict.fromkeys([*(current.get(key) or []), *value]))
                elif value not in (None, ""):
                    current[key] = value
            merged[match_index] = current
        return merged

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
