import copy
import inspect
import time
from datetime import datetime, timezone
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.checkpoint.base import uuid6
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.orm import Session

from app.agents.tools import AgentPlanner, bind_agent_tool
from app.core.config import get_settings
from app.core.redis_client import RedisUnavailableError, get_redis_client, redis_key
from app.models.entities import AgentArtifact, AgentRun, AgentStep
from app.models.entities import Application, Job, MatchResult, Profile, ResumeVersion
from app.models.schemas import AgentRunRequest
from app.services.approval_service import ApprovalService
from app.services.agent_reliability import (
    AgentTaskContractService,
    AgentTaskIncompleteError,
    format_completion_failure,
)
from app.services.application_service import ApplicationService
from app.services.execution_provenance import ExecutionProvenanceService
from app.services.interview_prep import InterviewPrepService
from app.services.job_search import JobSearchService
from app.services.langgraph_checkpointer import LangGraphCheckpointerLifecycle
from app.services.matcher import MatcherService
from app.services.memory_feedback import CareerMemoryService
from app.services.resume_tailor import ResumeTailorService
from app.services.run_control import RunControlService
from app.services.trace_service import TraceService


TaskType = Literal[
    "find_jobs_for_profile",
    "tailor_resume_for_job",
    "quick_apply",
    "prepare_interview_for_job",
    "full_career_flow",
]


class CareerAgentGraphState(TypedDict, total=False):
    request: dict[str, Any]
    run_id: int
    task_type: TaskType
    execution_plan: dict[str, Any]
    task_contract: dict[str, Any]
    memory_context: dict[str, Any]
    goal_ledger: list[dict[str, Any]]
    completion_verification: dict[str, Any]
    profile_id: int | None
    job_id: int | None
    resume_version_id: int | None
    query: str | None
    location: str | None
    limit: int
    graph_thread_id: str
    application_confirmed: bool
    job_ids: list[int]
    matches: list[dict[str, Any]]
    source_errors: dict[str, str]
    selected_job: dict[str, Any]
    selected_job_id: int
    match_result_id: int
    overall_score: float
    verification: dict[str, Any]
    fit_gate: dict[str, Any]
    human_confirmation: dict[str, Any]
    tailor: dict[str, Any]
    application: dict[str, Any]
    interview_prep: dict[str, Any]
    output: dict[str, Any]


class AgentRunCancelled(RuntimeError):
    pass


class LangGraphAgentOrchestrator:
    def __init__(
        self,
        *,
        trace: TraceService | None = None,
        job_search: JobSearchService | None = None,
        matcher: MatcherService | None = None,
        tailor: ResumeTailorService | None = None,
        application: ApplicationService | None = None,
        interview_prep: InterviewPrepService | None = None,
        planner: AgentPlanner | None = None,
        approvals: ApprovalService | None = None,
    ) -> None:
        self.trace = trace or TraceService()
        self.job_search = job_search or JobSearchService()
        self.matcher = matcher or MatcherService()
        self.tailor = tailor or ResumeTailorService()
        self.application = application or ApplicationService()
        self.interview_prep = interview_prep or InterviewPrepService()
        self.planner = planner or AgentPlanner()
        self.approvals = approvals or ApprovalService()
        self.task_contracts = AgentTaskContractService()
        self.settings = get_settings()
        self._runtime_dbs: dict[int, Session] = {}
        self._runtime_plans: dict[int, dict[str, Any]] = {}
        self._checkpoint_lifecycle = LangGraphCheckpointerLifecycle(settings=self.settings)
        self.checkpointer = None
        self._graph = None

    async def run(
        self,
        db: Session,
        request: AgentRunRequest,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ):
        started = time.perf_counter()
        graph_thread_id = f"agent-run-{uuid4().hex}"
        run = self.trace.create_run(
            db,
            task_type=request.task_type,
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
        return await self._execute_run(db, run, request, graph_thread_id, started)

    def queue_run(
        self,
        db: Session,
        request: AgentRunRequest,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> AgentRun:
        graph_thread_id = f"agent-run-{uuid4().hex}"
        return self.trace.create_run(
            db,
            task_type=request.task_type,
            profile_id=request.profile_id,
            job_id=request.job_id,
            tenant_id=tenant_id,
            user_id=user_id,
            status="queued",
            input_json={
                **request.model_dump(),
                "orchestration_framework": "langgraph",
                "graph_thread_id": graph_thread_id,
            },
        )

    async def run_existing(self, db: Session, run_id: int) -> AgentRun:
        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if run is None:
            raise ValueError(f"Agent run {run_id} not found.")
        if run.status not in {"queued", "running"}:
            raise ValueError(f"Agent run {run_id} cannot be started from status {run.status}.")
        payload = dict(run.input_json or {})
        request_payload = {key: payload.get(key) for key in AgentRunRequest.model_fields if key in payload}
        request = AgentRunRequest(**request_payload)
        graph_thread_id = self._graph_thread_id_from_run(run)
        started = time.perf_counter()
        execution_mode = str(payload.get("execution_mode") or "initial")
        return await self._execute_run(
            db,
            run,
            request,
            graph_thread_id,
            started,
            resume_from_checkpoint=execution_mode in {"checkpoint_resume", "checkpoint_rewind"},
        )

    async def _execute_run(
        self,
        db: Session,
        run: AgentRun,
        request: AgentRunRequest,
        graph_thread_id: str,
        started: float,
        resume_from_checkpoint: bool = False,
    ) -> AgentRun:
        self._runtime_dbs[run.id] = db
        run.status = "running"
        db.add(run)
        db.commit()
        self._raise_if_cancelled(db, run.id)
        self.trace.add_event(
            db,
            run_id=run.id,
            event_type="run_recovery_started" if resume_from_checkpoint else "run_started",
            payload={
                "task_type": request.task_type,
                "graph_thread_id": graph_thread_id,
                "execution_mode": (run.input_json or {}).get("execution_mode") or "initial",
                "recovery_attempt": int((run.input_json or {}).get("recovery_attempt") or 0),
            },
        )
        try:
            graph = await self._ensure_graph()
            config = {"configurable": {"thread_id": graph_thread_id}}
            graph_input: dict[str, Any] | Command | None = {
                "request": request.model_dump(),
                "run_id": run.id,
                "task_type": request.task_type,
                "profile_id": request.profile_id,
                "job_id": request.job_id,
                "resume_version_id": request.resume_version_id,
                "query": request.query,
                "location": request.location,
                "limit": request.limit,
                "application_confirmed": request.application_confirmed,
                "graph_thread_id": graph_thread_id,
            }
            if resume_from_checkpoint:
                snapshot = await graph.aget_state(config)
                if snapshot.values:
                    graph_input = None
                    self._runtime_plans[run.id] = (snapshot.values or {}).get("execution_plan") or {}
                    self.trace.add_event(
                        db,
                        run_id=run.id,
                        event_type="checkpoint_recovery_loaded",
                        payload={
                            "checkpoint_id": (snapshot.config or {}).get("configurable", {}).get("checkpoint_id"),
                            "next_nodes": list(snapshot.next or ()),
                            "has_interrupt": bool(getattr(snapshot, "interrupts", ()) or ()),
                        },
                    )
                else:
                    self.trace.add_event(
                        db,
                        run_id=run.id,
                        event_type="checkpoint_recovery_fallback_to_start",
                        payload={"reason": "checkpoint_not_found", "graph_thread_id": graph_thread_id},
                    )
            final_state = await self._invoke_graph(
                graph,
                graph_input,
                db=db,
                run_id=run.id,
                config=config,
            )
            self._raise_if_cancelled(db, run.id)
            interrupts = self._interrupt_payloads(final_state)
            if interrupts:
                output = self._confirmation_output(
                    run,
                    interrupts=interrupts,
                    graph_thread_id=graph_thread_id,
                    execution_plan=final_state.get("execution_plan") or {},
                )
                self.trace.add_artifact(db, run_id=run.id, artifact_type="human_interrupt", payload=output)
                return self.trace.finish_run(
                    db,
                    run=run,
                    status="waiting_for_confirmation",
                    output_json=output,
                    started_at=started,
                )
            output = dict(final_state.get("output") or {})
            output["execution_plan"] = final_state.get("execution_plan") or {}
            output["orchestration_framework"] = "langgraph"
            output["graph_thread_id"] = graph_thread_id
            if resume_from_checkpoint:
                output["recovery"] = {
                    "mode": (run.input_json or {}).get("execution_mode"),
                    "attempt": int((run.input_json or {}).get("recovery_attempt") or 0),
                    "checkpoint_id": final_state.get("checkpoint_id"),
                }
            return self.trace.finish_run(db, run=run, status="completed", output_json=output, started_at=started)
        except AgentRunCancelled as exc:
            db.expire_all()
            current_run = db.query(AgentRun).filter(AgentRun.id == run.id).first() or run
            if current_run.status == "withdrawn":
                withdrawal = (current_run.output_json or {}).get("withdrawal") or {}
                reconciled, _ = RunControlService().withdraw(
                    db,
                    run=current_run,
                    reason=str(withdrawal.get("reason") or "用户撤回流程"),
                    actor="worker_withdrawal_reconciliation",
                )
                self.trace.add_event(
                    db,
                    run_id=reconciled.id,
                    event_type="withdrawal_reconciled_after_worker_stop",
                    payload={"error": str(exc)},
                )
                return reconciled
            return self.trace.finish_run(
                db,
                run=run,
                status="cancelled",
                output_json={
                    "cancelled": True,
                    "error": str(exc),
                    "orchestration_framework": "langgraph",
                    "graph_thread_id": graph_thread_id,
                    "execution_plan": self._runtime_plans.get(run.id) or {},
                },
                error_message=str(exc),
                error_exception=exc,
                started_at=started,
            )
        except GraphInterrupt as exc:
            interrupts = self._interrupt_payloads_from_exception(exc)
            output = self._confirmation_output(
                run,
                interrupts=interrupts,
                graph_thread_id=graph_thread_id,
                execution_plan=self._runtime_plans.get(run.id) or {},
            )
            self.trace.add_artifact(db, run_id=run.id, artifact_type="human_interrupt", payload=output)
            return self.trace.finish_run(
                db,
                run=run,
                status="waiting_for_confirmation",
                output_json=output,
                started_at=started,
            )
        except Exception as exc:  # noqa: BLE001
            return self.trace.finish_run(
                db,
                run=run,
                status="failed",
                output_json={
                    "error": str(exc),
                    "orchestration_framework": "langgraph",
                    "graph_thread_id": graph_thread_id,
                    "execution_plan": self._runtime_plans.get(run.id) or {},
                },
                error_message=str(exc),
                error_exception=exc,
                started_at=started,
            )
        finally:
            self._runtime_dbs.pop(run.id, None)
            self._runtime_plans.pop(run.id, None)
            await self._close_checkpoint()

    async def resume(self, db: Session, run_id: int, resume_payload: dict[str, Any]) -> AgentRun:
        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if run is None:
            raise ValueError(f"Agent run {run_id} not found.")
        if run.status != "waiting_for_confirmation":
            raise ValueError(f"Agent run {run_id} is not waiting for confirmation.")
        graph_thread_id = self._graph_thread_id_from_run(run)
        started = time.perf_counter()
        self._runtime_dbs[run.id] = db
        self._runtime_plans[run.id] = (run.output_json or {}).get("execution_plan") or {}
        run.status = "running"
        db.add(run)
        db.commit()
        self._raise_if_cancelled(db, run.id)
        self.trace.add_event(
            db,
            run_id=run.id,
            event_type="run_resumed",
            payload={"graph_thread_id": graph_thread_id, "resume_payload": resume_payload},
        )
        try:
            graph = await self._ensure_graph()
            final_state = await self._invoke_graph(
                graph,
                Command(resume=resume_payload),
                db=db,
                run_id=run.id,
                config={"configurable": {"thread_id": graph_thread_id}},
            )
            self._raise_if_cancelled(db, run.id)
            interrupts = self._interrupt_payloads(final_state)
            if interrupts:
                output = self._confirmation_output(
                    run,
                    interrupts=interrupts,
                    graph_thread_id=graph_thread_id,
                    execution_plan=final_state.get("execution_plan") or self._runtime_plans.get(run.id) or {},
                )
                return self.trace.finish_run(
                    db,
                    run=run,
                    status="waiting_for_confirmation",
                    output_json=output,
                    started_at=started,
                )
            output = dict(final_state.get("output") or {})
            output["execution_plan"] = final_state.get("execution_plan") or self._runtime_plans.get(run.id) or {}
            output["orchestration_framework"] = "langgraph"
            output["graph_thread_id"] = graph_thread_id
            return self.trace.finish_run(db, run=run, status="completed", output_json=output, started_at=started)
        except AgentRunCancelled as exc:
            db.expire_all()
            current_run = db.query(AgentRun).filter(AgentRun.id == run.id).first() or run
            if current_run.status == "withdrawn":
                withdrawal = (current_run.output_json or {}).get("withdrawal") or {}
                reconciled, _ = RunControlService().withdraw(
                    db,
                    run=current_run,
                    reason=str(withdrawal.get("reason") or "用户撤回流程"),
                    actor="worker_withdrawal_reconciliation",
                )
                self.trace.add_event(
                    db,
                    run_id=reconciled.id,
                    event_type="withdrawal_reconciled_after_worker_stop",
                    payload={"error": str(exc)},
                )
                return reconciled
            return self.trace.finish_run(
                db,
                run=run,
                status="cancelled",
                output_json={
                    "cancelled": True,
                    "error": str(exc),
                    "orchestration_framework": "langgraph",
                    "graph_thread_id": graph_thread_id,
                    "execution_plan": self._runtime_plans.get(run.id) or {},
                },
                error_message=str(exc),
                error_exception=exc,
                started_at=started,
            )
        except GraphInterrupt as exc:
            interrupts = self._interrupt_payloads_from_exception(exc)
            output = self._confirmation_output(
                run,
                interrupts=interrupts,
                graph_thread_id=graph_thread_id,
                execution_plan=self._runtime_plans.get(run.id) or {},
            )
            self.trace.add_artifact(db, run_id=run.id, artifact_type="human_interrupt", payload=output)
            return self.trace.finish_run(
                db,
                run=run,
                status="waiting_for_confirmation",
                output_json=output,
                started_at=started,
            )
        except Exception as exc:  # noqa: BLE001
            return self.trace.finish_run(
                db,
                run=run,
                status="failed",
                output_json={
                    "error": str(exc),
                    "orchestration_framework": "langgraph",
                    "graph_thread_id": graph_thread_id,
                    "execution_plan": self._runtime_plans.get(run.id) or {},
                },
                error_message=str(exc),
                error_exception=exc,
                started_at=started,
            )
        finally:
            self._runtime_dbs.pop(run.id, None)
            self._runtime_plans.pop(run.id, None)
            await self._close_checkpoint()

    def cancel(self, db: Session, run_id: int, *, reason: str | None = None) -> AgentRun:
        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if run is None:
            raise ValueError(f"Agent run {run_id} not found.")
        if run.status not in {"queued", "running", "waiting_for_confirmation"}:
            raise ValueError(f"Agent run {run_id} cannot be cancelled from status {run.status}.")
        self.trace.add_event(
            db,
            run_id=run.id,
            event_type="run_cancel_requested",
            payload={"reason": reason, "previous_status": run.status},
        )
        self.approvals.cancel_pending_for_run(db, run_id=run.id, note=reason or "run cancelled")
        output = dict(run.output_json or {})
        output.update({"cancelled": True, "cancel_reason": reason, "previous_status": run.status})
        run.status = "cancelled"
        run.output_json = output
        run.error_message = reason or "Agent run cancelled by user."
        db.add(run)
        db.commit()
        db.refresh(run)
        self._set_redis_cancel_flag(run.id)
        self.trace.add_event(
            db,
            run_id=run.id,
            event_type="run_cancelled",
            payload={"reason": reason, "output_json": output},
        )
        return run

    async def graph_state(self, run: AgentRun) -> dict[str, Any]:
        graph_thread_id = self._graph_thread_id_from_run(run)
        graph = await self._ensure_graph()
        try:
            snapshot = await graph.aget_state({"configurable": {"thread_id": graph_thread_id}})
            return {
                "run_id": run.id,
                "graph_thread_id": graph_thread_id,
                "next": list(snapshot.next or ()),
                "values": self.trace._json_safe(snapshot.values or {}),
                "interrupts": [
                    {"id": item.id, "value": item.value}
                    for item in getattr(snapshot, "interrupts", ()) or ()
                ],
                "checkpoint_id": (snapshot.config or {}).get("configurable", {}).get("checkpoint_id"),
            }
        finally:
            await self._close_checkpoint()

    async def checkpoint_history(self, run: AgentRun, *, limit: int = 50) -> list[dict[str, Any]]:
        graph_thread_id = self._graph_thread_id_from_run(run)
        graph = await self._ensure_graph()
        history: list[dict[str, Any]] = []
        try:
            async for snapshot in graph.aget_state_history(
                {"configurable": {"thread_id": graph_thread_id}},
                limit=limit,
            ):
                values = dict(snapshot.values or {})
                if values.get("run_id") not in {None, run.id}:
                    continue
                config = (snapshot.config or {}).get("configurable", {})
                parent = (snapshot.parent_config or {}).get("configurable", {})
                checkpoint_id = str(config.get("checkpoint_id") or "")
                if not checkpoint_id:
                    continue
                next_nodes = [str(item) for item in snapshot.next or ()]
                interrupts = list(getattr(snapshot, "interrupts", ()) or ())
                history.append(
                    {
                        "checkpoint_id": checkpoint_id,
                        "parent_checkpoint_id": parent.get("checkpoint_id"),
                        "created_at": snapshot.created_at,
                        "step": (snapshot.metadata or {}).get("step"),
                        "next_nodes": next_nodes,
                        "state_summary": self._checkpoint_state_summary(values),
                        "has_interrupt": bool(interrupts),
                        "replayable": bool(next_nodes),
                    }
                )
            return history
        finally:
            await self._close_checkpoint()

    async def rewind_from_checkpoint(
        self,
        db: Session,
        *,
        run_id: int,
        checkpoint_id: str,
        actor: str | None = None,
        reason: str | None = None,
    ) -> AgentRun:
        source_run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if source_run is None:
            raise ValueError(f"Agent run {run_id} not found.")
        if source_run.status == "running":
            raise ValueError("A running Agent run must be cancelled or completed before checkpoint rewind.")
        if source_run.status == "withdrawn":
            raise ValueError("A withdrawn Agent run cannot be used as a checkpoint rewind source.")

        source_thread_id = self._graph_thread_id_from_run(source_run)
        graph = await self._ensure_graph()
        control = RunControlService()
        action = control.create_action(
            db,
            run_id=source_run.id,
            action_type="checkpoint_rewind",
            actor=actor,
            source_checkpoint_id=checkpoint_id,
            payload={"reason": reason},
        )
        fork_run: AgentRun | None = None
        new_thread_id: str | None = None
        try:
            source_config = {
                "configurable": {
                    "thread_id": source_thread_id,
                    "checkpoint_id": checkpoint_id,
                }
            }
            snapshot = await graph.aget_state(source_config)
            actual_checkpoint_id = (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
            if actual_checkpoint_id != checkpoint_id or not snapshot.values:
                raise ValueError(f"Checkpoint {checkpoint_id} was not found for Agent run {run_id}.")
            if int((snapshot.values or {}).get("run_id") or 0) != source_run.id:
                raise ValueError("Checkpoint does not belong to the selected Agent run.")
            if not snapshot.next:
                raise ValueError("The selected checkpoint is terminal; choose an earlier checkpoint with a next node.")

            checkpoint_tuple = await self.checkpointer.aget_tuple(source_config)
            if checkpoint_tuple is None:
                raise ValueError(f"Checkpoint {checkpoint_id} payload was not found.")

            new_thread_id = f"agent-run-{uuid4().hex}"
            source_input = dict(source_run.input_json or {})
            request_payload = {
                key: source_input.get(key)
                for key in AgentRunRequest.model_fields
                if key in source_input
            }
            fork_input = {
                **request_payload,
                "orchestration_framework": "langgraph",
                "graph_thread_id": new_thread_id,
                "execution_mode": "checkpoint_rewind",
                "rewind_of_run_id": source_run.id,
                "origin_checkpoint_id": checkpoint_id,
                "rewind_reason": reason,
            }
            fork_run = self.trace.create_run(
                db,
                task_type=source_run.task_type,
                profile_id=source_run.profile_id,
                job_id=source_run.job_id,
                status="queued",
                input_json=fork_input,
            )
            fork_run.tenant_id = source_run.tenant_id
            db.add(fork_run)
            db.commit()
            db.refresh(fork_run)

            checkpoint = copy.deepcopy(checkpoint_tuple.checkpoint)
            checkpoint["id"] = str(uuid6())
            checkpoint["ts"] = datetime.now(timezone.utc).isoformat()
            channel_values = checkpoint.setdefault("channel_values", {})
            channel_values["run_id"] = fork_run.id
            channel_values["graph_thread_id"] = new_thread_id
            execution_plan = dict(channel_values.get("execution_plan") or {})
            if execution_plan:
                execution_plan["graph_thread_id"] = new_thread_id
                execution_plan["rewind_of_run_id"] = source_run.id
                execution_plan["origin_checkpoint_id"] = checkpoint_id
                channel_values["execution_plan"] = execution_plan
            metadata = {
                **(checkpoint_tuple.metadata or {}),
                "source": "checkpoint_rewind",
                "parents": {},
                "source_run_id": source_run.id,
                "source_checkpoint_id": checkpoint_id,
            }
            fork_config = await self.checkpointer.aput(
                {"configurable": {"thread_id": new_thread_id, "checkpoint_ns": ""}},
                checkpoint,
                metadata,
                checkpoint.get("channel_versions") or {},
            )
            fork_checkpoint_id = fork_config.get("configurable", {}).get("checkpoint_id")
            fork_run.input_json = {
                **(fork_run.input_json or {}),
                "fork_checkpoint_id": fork_checkpoint_id,
            }
            db.add(fork_run)
            db.commit()
            db.refresh(fork_run)
            inherited = self._inherit_checkpoint_provenance(
                db,
                source_run=source_run,
                fork_run=fork_run,
                checkpoint_created_at=snapshot.created_at,
            )

            event_payload = {
                "source_run_id": source_run.id,
                "source_checkpoint_id": checkpoint_id,
                "target_run_id": fork_run.id,
                "target_graph_thread_id": new_thread_id,
                "target_checkpoint_id": fork_checkpoint_id,
                "next_nodes": list(snapshot.next or ()),
                "reason": reason,
                "actor": actor,
                "inherited_provenance": inherited,
            }
            self.trace.add_event(
                db,
                run_id=source_run.id,
                event_type="checkpoint_rewind_forked",
                payload=event_payload,
            )
            self.trace.add_event(
                db,
                run_id=fork_run.id,
                event_type="checkpoint_rewind_created",
                payload=event_payload,
            )
            control.complete_action(
                db,
                action,
                status="completed",
                target_run_id=fork_run.id,
                payload=event_payload,
            )
            return fork_run
        except Exception as exc:
            if fork_run is not None:
                fork_run.status = "failed"
                fork_run.error_message = f"Checkpoint rewind creation failed: {exc}"
                db.add(fork_run)
                db.commit()
            control.complete_action(
                db,
                action,
                status="failed",
                target_run_id=fork_run.id if fork_run else None,
                payload={"error": f"{exc.__class__.__name__}: {exc}"},
            )
            if new_thread_id and self.checkpointer is not None:
                await self.checkpointer.adelete_thread(new_thread_id)
            raise
        finally:
            await self._close_checkpoint()

    def _checkpoint_state_summary(self, values: dict[str, Any]) -> dict[str, Any]:
        artifact_fields = {
            "job_count": len(values.get("job_ids") or []),
            "match_count": len(values.get("matches") or []),
            "has_tailored_resume": bool(values.get("resume_version_id") or values.get("tailor")),
            "has_application_packet": bool(values.get("application")),
            "has_interview_prep": bool(values.get("interview_prep")),
        }
        return {
            "run_id": values.get("run_id"),
            "task_type": values.get("task_type"),
            "profile_id": values.get("profile_id"),
            "job_id": values.get("job_id"),
            "selected_job_id": values.get("selected_job_id"),
            "overall_score": values.get("overall_score"),
            **artifact_fields,
        }

    def _inherit_checkpoint_provenance(
        self,
        db: Session,
        *,
        source_run: AgentRun,
        fork_run: AgentRun,
        checkpoint_created_at: str | None,
    ) -> dict[str, Any]:
        if not checkpoint_created_at:
            raise ValueError("Checkpoint provenance cannot be inherited without checkpoint_created_at.")
        try:
            cutoff = datetime.fromisoformat(str(checkpoint_created_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                f"Checkpoint provenance has an invalid created_at value: {checkpoint_created_at}"
            ) from exc

        def before_checkpoint(value: datetime) -> bool:
            left = value
            right = cutoff
            if left.tzinfo is None and right.tzinfo is not None:
                left = left.replace(tzinfo=timezone.utc)
            elif left.tzinfo is not None and right.tzinfo is None:
                right = right.replace(tzinfo=timezone.utc)
            return left <= right

        source_steps = (
            db.query(AgentStep)
            .filter(AgentStep.run_id == source_run.id)
            .order_by(AgentStep.id.asc())
            .all()
        )
        inherited_steps = [
            {
                "step_name": step.step_name,
                "tool_name": step.tool_name,
                "status": step.status,
                "input_json": step.input_json or {},
                "output_json": step.output_json or {},
                "latency_ms": step.latency_ms,
                "source_step_id": step.id,
            }
            for step in source_steps
            if before_checkpoint(step.created_at)
        ]
        source_artifacts = (
            db.query(AgentArtifact)
            .filter(AgentArtifact.run_id == source_run.id)
            .order_by(AgentArtifact.id.asc())
            .all()
        )
        copied_artifacts: list[str] = []
        for artifact in source_artifacts:
            if not before_checkpoint(artifact.created_at):
                continue
            payload = dict(artifact.artifact_json or {})
            payload["checkpoint_inheritance"] = {
                "source_run_id": source_run.id,
                "source_artifact_id": artifact.id,
                "source_checkpoint_created_at": checkpoint_created_at,
            }
            db.add(
                AgentArtifact(
                    run_id=fork_run.id,
                    artifact_type=artifact.artifact_type,
                    artifact_json=payload,
                )
            )
            copied_artifacts.append(artifact.artifact_type)
        db.add(
            AgentArtifact(
                run_id=fork_run.id,
                artifact_type="checkpoint_inherited_trajectory",
                artifact_json={
                    "source_run_id": source_run.id,
                    "source_checkpoint_created_at": checkpoint_created_at,
                    "steps": inherited_steps,
                    "artifact_types": copied_artifacts,
                },
            )
        )
        db.commit()
        return {
            "source_run_id": source_run.id,
            "step_count": len(inherited_steps),
            "artifact_types": copied_artifacts,
        }

    async def _invoke_graph(
        self,
        graph,
        payload: dict[str, Any] | Command | None,
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
            interrupts = list(getattr(snapshot, "interrupts", ()) or ())
            if interrupts:
                state["__interrupt__"] = interrupts
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

    def _record_langgraph_event(self, db: Session, *, run_id: int, event: dict[str, Any]) -> None:
        event_name = str(event.get("event") or "")
        node_name = str(event.get("name") or "")
        data = event.get("data") or {}
        if node_name and node_name.startswith("Channel"):
            return
        if event_name == "on_chain_start":
            event_type = "graph_started" if node_name == "LangGraph" else "graph_node_started"
        elif event_name == "on_chain_end":
            event_type = "graph_completed" if node_name == "LangGraph" else "graph_node_completed"
        elif event_name == "on_chain_stream":
            chunk = data.get("chunk") if isinstance(data, dict) else None
            if isinstance(chunk, dict) and "__interrupt__" in chunk:
                event_type = "graph_interrupt"
            else:
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
            return {
                "id": str(getattr(value, "id")),
                "value": self.trace._json_safe(getattr(value, "value")),
            }
        if isinstance(value, tuple):
            return [self._json_safe_graph_value(item) for item in value]
        if isinstance(value, list):
            return [self._json_safe_graph_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._json_safe_graph_value(item) for key, item in value.items()}
        return self.trace._json_safe(value)

    def _build_graph(self):
        graph = StateGraph(CareerAgentGraphState)
        graph.add_node("plan_task", self._node_plan_task)
        graph.add_node("load_profile", self._node_load_profile)
        graph.add_node("search_jobs", self._node_search_jobs)
        graph.add_node("match_jobs", self._node_match_jobs)
        graph.add_node("select_job", self._node_select_job)
        graph.add_node("load_job", self._node_load_job)
        graph.add_node("match_job", self._node_match_job)
        graph.add_node("tailor_resume", self._node_tailor_resume)
        graph.add_node("fit_gate", self._node_fit_gate)
        graph.add_node("ensure_resume_version", self._node_ensure_resume_version)
        graph.add_node("create_application_packet", self._node_create_application_packet)
        graph.add_node("generate_interview_prep", self._node_generate_interview_prep)
        graph.add_node("finalize_find_jobs", self._node_finalize_find_jobs)
        graph.add_node("finalize_tailor", self._node_finalize_tailor)
        graph.add_node("finalize_quick_apply", self._node_finalize_quick_apply)
        graph.add_node("finalize_interview", self._node_finalize_interview)
        graph.add_node("finalize_full_flow", self._node_finalize_full_flow)
        graph.add_node("completion_gate", self._node_completion_gate)

        graph.add_edge(START, "plan_task")
        graph.add_conditional_edges(
            "plan_task",
            self._route_after_plan,
            {
                "find_jobs_for_profile": "load_profile",
                "tailor_resume_for_job": "load_profile",
                "quick_apply": "load_profile",
                "prepare_interview_for_job": "load_profile",
                "full_career_flow": "load_profile",
            },
        )
        graph.add_conditional_edges(
            "load_profile",
            self._route_after_profile,
            {
                "search_jobs": "search_jobs",
                "load_job": "load_job",
            },
        )
        graph.add_edge("search_jobs", "match_jobs")
        graph.add_conditional_edges(
            "match_jobs",
            self._route_after_match_jobs,
            {
                "finalize_find_jobs": "finalize_find_jobs",
                "select_job": "select_job",
            },
        )
        graph.add_edge("select_job", "load_job")
        graph.add_edge("load_job", "match_job")
        graph.add_conditional_edges(
            "match_job",
            self._route_after_match_job,
            {
                "tailor_resume": "tailor_resume",
                "fit_gate": "fit_gate",
                "generate_interview_prep": "generate_interview_prep",
            },
        )
        graph.add_conditional_edges(
            "tailor_resume",
            self._route_after_tailor,
            {
                "finalize_tailor": "finalize_tailor",
                "fit_gate": "fit_gate",
            },
        )
        graph.add_edge("fit_gate", "ensure_resume_version")
        graph.add_edge("ensure_resume_version", "create_application_packet")
        graph.add_conditional_edges(
            "create_application_packet",
            self._route_after_application,
            {
                "finalize_quick_apply": "finalize_quick_apply",
                "generate_interview_prep": "generate_interview_prep",
            },
        )
        graph.add_conditional_edges(
            "generate_interview_prep",
            self._route_after_interview,
            {
                "finalize_interview": "finalize_interview",
                "finalize_full_flow": "finalize_full_flow",
            },
        )
        graph.add_edge("finalize_find_jobs", "completion_gate")
        graph.add_edge("finalize_tailor", "completion_gate")
        graph.add_edge("finalize_quick_apply", "completion_gate")
        graph.add_edge("finalize_interview", "completion_gate")
        graph.add_edge("finalize_full_flow", "completion_gate")
        graph.add_edge("completion_gate", END)
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

    def _request(self, state: CareerAgentGraphState) -> AgentRunRequest:
        return AgentRunRequest(**state["request"])

    async def _node_plan_task(self, state: CareerAgentGraphState) -> dict[str, Any]:
        request = self._request(state)
        db = self._db_from_state(state)
        plan = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="plan_task",
            input_json={"task_type": request.task_type},
            tool=bind_agent_tool(
                "LangGraph.AgentPlanner",
                lambda: self._async_value(
                    {
                        **self.planner.build_plan(request),
                        "orchestration_framework": "langgraph",
                        "graph_thread_id": state.get("graph_thread_id"),
                        "checkpoint_backend": self.settings.langgraph_checkpoint_backend,
                        "interrupt_policy": "quick_apply_requires_application_confirmation",
                    }
                ),
            ),
        )
        run = db.query(AgentRun).filter(AgentRun.id == state["run_id"]).one()
        memory_context = CareerMemoryService().compact_context(
            db,
            tenant_id=run.tenant_id,
            user_id=run.user_id,
            profile_id=state.get("profile_id"),
        )
        provenance = ExecutionProvenanceService().build(task_type=request.task_type, plan=plan)
        plan["memory_context"] = {
            "version": memory_context["version"],
            "item_count": memory_context["item_count"],
            "context_chars": memory_context["context_chars"],
        }
        plan["execution_provenance"] = provenance
        self._runtime_plans[state["run_id"]] = plan
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="execution_plan", payload=plan)
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
        task_contract = self.task_contracts.build_contract(request.task_type, request.model_dump())
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="task_contract",
            payload=task_contract,
        )
        return {
            "execution_plan": plan,
            "task_contract": task_contract,
            "memory_context": memory_context,
        }

    async def _node_load_profile(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="load_profile",
            input_json={"profile_id": state.get("profile_id")},
            tool=bind_agent_tool(
                "profile_repository.load_profile",
                lambda: self._load_profile(db, state.get("profile_id")),
            ),
        )
        query = state.get("query") or " ".join(profile.target_roles_json or []) or "Agent 开发实习生"
        return {"profile_id": profile.id, "query": query}

    async def _node_search_jobs(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        jobs, source_errors = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="search_jobs",
            input_json={"query": state.get("query"), "location": state.get("location"), "limit": state.get("limit")},
            tool=bind_agent_tool(
                "job_search.search_jobs",
                lambda: self.job_search.search(
                    db,
                    query=state.get("query") or "Agent 开发实习生",
                    location=state.get("location"),
                    internship_only=True,
                    limit=int(state.get("limit") or 20),
                    store_results=True,
                ),
            ),
        )
        return {"job_ids": [job.id for job in jobs], "source_errors": source_errors}

    async def _node_match_jobs(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        matches: list[dict[str, Any]] = []
        for job_id in state.get("job_ids", []):
            job = await self._load_job(db, int(job_id))
            idempotency_key = self._idempotency_key(state, "match_search", profile.id, job.id)
            match = await self.trace.step(
                db,
                run_id=state["run_id"],
                step_name=f"match_job_{job.id}",
                input_json={"profile_id": profile.id, "job_id": job.id},
                tool=bind_agent_tool(
                    "matcher.match_job",
                    lambda job=job, key=idempotency_key: self._async_value(
                        self._create_match_result(db, profile, job, idempotency_key=key)
                    ),
                ),
            )
            matches.append(
                {
                    "job_id": job.id,
                    "match_result_id": match.id,
                    "title": job.title,
                    "company": job.company,
                    "overall_score": match.overall_score,
                    "matched_skills": match.matched_skills_json,
                    "missing_skills": match.missing_skills_json,
                    "apply_url": job.apply_url,
                }
            )
        matches.sort(key=lambda item: item["overall_score"], reverse=True)
        payload = {
            "profile_id": profile.id,
            "query": state.get("query"),
            "matches": matches,
            "source_errors": state.get("source_errors") or {},
        }
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="ranked_jobs", payload=payload)
        return {"matches": matches}

    async def _node_select_job(self, state: CareerAgentGraphState) -> dict[str, Any]:
        matches = state.get("matches") or []
        if not matches:
            raise ValueError(
                "Full career flow stopped: no matched jobs found. "
                f"source_errors={state.get('source_errors') or {}}"
            )
        selection = interrupt(
            {
                "kind": "job_selection",
                "message": "岗位已经检索并完成初步匹配，请选择一个岗位后再继续生成材料。",
                "matches": matches,
                "required_action": "select_job",
            }
        )
        selected_job_id = int(selection.get("job_id") if isinstance(selection, dict) else selection)
        selected = next((item for item in matches if int(item["job_id"]) == selected_job_id), None)
        if selected is None:
            raise ValueError(f"Selected job #{selected_job_id} is not in the current search results.")
        selected_job = dict(selected)
        db = self._db_from_state(state)
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="selected_job",
            payload={"selection_policy": "human_selected", "selected_job": selected_job},
        )
        return {"selected_job": selected_job, "job_id": int(selected_job["job_id"]), "selected_job_id": int(selected_job["job_id"])}

    async def _node_load_job(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        job = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="load_job",
            input_json={"job_id": state.get("job_id")},
            tool=bind_agent_tool(
                "job_repository.load_job",
                lambda: self._load_job(db, state.get("job_id")),
            ),
        )
        return {"job_id": job.id}

    async def _node_match_job(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        job = await self._load_job(db, state.get("job_id"))
        selected_match_id = int((state.get("selected_job") or {}).get("match_result_id") or 0)
        selected_match = None
        if selected_match_id:
            selected_match = db.query(MatchResult).filter(MatchResult.id == selected_match_id).first()
            if (
                selected_match is None
                or selected_match.profile_id != profile.id
                or selected_match.job_id != job.id
            ):
                raise ValueError(
                    f"Selected match result #{selected_match_id} does not belong to profile #{profile.id} "
                    f"and job #{job.id}."
                )
        idempotency_key = self._idempotency_key(state, "match_primary", profile.id, job.id)
        if selected_match is not None:
            async def match_handler():
                return selected_match
        else:
            async def match_handler():
                return self._create_match_result(db, profile, job, idempotency_key=idempotency_key)
        match = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="match_job",
            input_json={
                "profile_id": profile.id,
                "job_id": job.id,
                "reuse_match_result_id": selected_match_id or None,
            },
            tool=bind_agent_tool("matcher.match_job", match_handler),
        )
        payload = {
            "match_result_id": match.id,
            "overall_score": match.overall_score,
            "matched_skills": match.matched_skills_json,
            "missing_skills": match.missing_skills_json,
            "match_reused": selected_match is not None,
        }
        if state["task_type"] == "full_career_flow":
            payload["selected_job"] = {
                "job_id": job.id,
                "title": job.title,
                "company": job.company,
                "overall_score": match.overall_score,
                "matched_skills": match.matched_skills_json,
                "missing_skills": match.missing_skills_json,
                "apply_url": job.apply_url,
            }
            payload["selected_job_id"] = job.id
        return payload

    async def _node_tailor_resume(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        job = await self._load_job(db, state.get("job_id"))
        key = self._idempotency_key(state, "resume", profile.id, job.id)
        existing = self._resume_by_idempotency_key(db, key)
        if existing is not None:
            self._record_idempotency_reuse(db, state["run_id"], "resume_version", key, existing.id)
            result = self._tailor_payload(state, profile, job, existing, idempotency_reused=True)
            self.trace.add_artifact(
                db,
                run_id=state["run_id"],
                artifact_type="tailored_resume",
                payload=result["tailor"],
            )
            return result
        version = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="tailor_resume_with_rag",
            input_json={"profile_id": profile.id, "job_id": job.id},
            tool=bind_agent_tool(
                "resume_tailor.tailor_resume",
                lambda: self._tailor_resume_with_idempotency(db, profile, job, key),
            ),
        )
        self._assign_idempotency_key(db, version, key)
        payload = self._tailor_payload(state, profile, job, version, idempotency_reused=False)["tailor"]
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="tailored_resume", payload=payload)
        return {
            "resume_version_id": version.id,
            "verification": version.verification_json,
            "tailor": payload,
        }

    async def _node_fit_gate(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        job = await self._load_job(db, state.get("job_id"))
        fit_gate = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="fit_gate",
            input_json={"profile_id": profile.id, "job_id": job.id, "min_score": 55},
            tool=bind_agent_tool(
                "matcher.enforce_fit_gate",
                lambda: self._async_value(self._fit_gate(db, profile, job, state=state)),
            ),
        )
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="fit_gate", payload=fit_gate)
        return {"fit_gate": fit_gate}

    async def _node_ensure_resume_version(self, state: CareerAgentGraphState) -> dict[str, Any]:
        if state.get("resume_version_id"):
            db = self._db_from_state(state)
            version = db.query(ResumeVersion).filter(ResumeVersion.id == int(state["resume_version_id"])).first()
            if version is None:
                raise ValueError(f"ResumeVersion {state['resume_version_id']} not found.")
            if version.lifecycle_status != "active":
                raise ValueError(f"ResumeVersion {version.id} is withdrawn and cannot be used for application materials.")
            return {"resume_version_id": version.id}
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        job = await self._load_job(db, state.get("job_id"))
        key = self._idempotency_key(state, "resume", profile.id, job.id)
        existing = self._resume_by_idempotency_key(db, key)
        if existing is not None:
            self._record_idempotency_reuse(db, state["run_id"], "resume_version", key, existing.id)
            return {"resume_version_id": existing.id}
        version = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="create_missing_tailored_resume",
            input_json={"profile_id": profile.id, "job_id": job.id},
            tool=bind_agent_tool(
                "resume_tailor.tailor_resume",
                lambda: self._tailor_resume_with_idempotency(db, profile, job, key),
            ),
        )
        self._assign_idempotency_key(db, version, key)
        return {"resume_version_id": version.id}

    async def _node_create_application_packet(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        job = await self._load_job(db, state.get("job_id"))
        resume_version = db.query(ResumeVersion).filter(ResumeVersion.id == state.get("resume_version_id")).first()
        if resume_version is None:
            raise ValueError(f"ResumeVersion {state.get('resume_version_id')} not found.")
        if resume_version.lifecycle_status != "active":
            raise ValueError(f"ResumeVersion {resume_version.id} is withdrawn and cannot be used for application materials.")
        confirmation = self._application_confirmation(state, job, resume_version)
        if not confirmation.get("confirmed"):
            raise ValueError("Application confirmation rejected by user.")
        key = self._idempotency_key(state, "application", profile.id, job.id, resume_version.id)
        existing = self._application_by_idempotency_key(db, key)
        if existing is not None:
            self._record_idempotency_reuse(db, state["run_id"], "application", key, existing.id)
            payload = self._application_payload(existing)
            payload["fit_gate"] = state.get("fit_gate")
            payload["human_confirmation"] = confirmation
            payload["idempotency_reused"] = True
            self.trace.add_artifact(
                db,
                run_id=state["run_id"],
                artifact_type="application_packet",
                payload=payload,
            )
            return {"application": payload}
        application = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="create_application_packet",
            input_json={
                "profile_id": profile.id,
                "job_id": job.id,
                "resume_version_id": resume_version.id,
                "approval_id": confirmation["approval_id"],
            },
            tool=bind_agent_tool(
                "application.create_quick_apply_packet",
                lambda: self.application.create_quick_apply_packet(
                    db,
                    **self._supported_kwargs(
                        self.application.create_quick_apply_packet,
                        profile=profile,
                        job=job,
                        resume_version=resume_version,
                        browser_assist=False,
                        idempotency_key=key,
                    ),
                ),
            ),
        )
        self._assign_idempotency_key(db, application, key)
        payload = self._application_payload(application)
        payload["fit_gate"] = state.get("fit_gate")
        payload["human_confirmation"] = confirmation
        payload["idempotency_reused"] = False
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="application_packet",
            payload=payload,
        )
        return {"application": payload}

    async def _node_generate_interview_prep(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        job = await self._load_job(db, state.get("job_id"))
        key = self._idempotency_key(state, "interview_prep", profile.id, job.id)
        existing = self._interview_prep_by_idempotency_key(db, key)
        if existing is not None:
            self._record_idempotency_reuse(db, state["run_id"], "interview_prep", key, existing.id)
            payload = self._interview_prep_payload(existing)
            payload["idempotency_reused"] = True
            self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="interview_prep", payload=payload)
            return {"interview_prep": payload}
        match_result = None
        if state.get("match_result_id"):
            match_result = db.query(MatchResult).filter(MatchResult.id == int(state["match_result_id"])).first()
        if match_result is None:
            match_result = self._create_match_result(
                db,
                profile,
                job,
                idempotency_key=self._idempotency_key(state, "match_interview", profile.id, job.id),
            )
        prep = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="generate_interview_prep",
            input_json={"profile_id": profile.id, "job_id": job.id, "match_result_id": match_result.id},
            tool=bind_agent_tool(
                "interview_prep.generate_packet",
                lambda: self.interview_prep.create_interview_prep_with_llm(
                    db,
                    **self._supported_kwargs(
                        self.interview_prep.create_interview_prep_with_llm,
                        profile=profile,
                        job=job,
                        match_result=match_result,
                        idempotency_key=key,
                    ),
                ),
            ),
        )
        self._assign_idempotency_key(db, prep, key)
        payload = self._interview_prep_payload(prep)
        payload["idempotency_reused"] = False
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="interview_prep", payload=payload)
        return {"interview_prep": payload}

    async def _node_finalize_find_jobs(self, state: CareerAgentGraphState) -> dict[str, Any]:
        self._db_from_state(state)
        return {
            "output": {
                "profile_id": state.get("profile_id"),
                "query": state.get("query"),
                "matches": state.get("matches") or [],
                "source_errors": state.get("source_errors") or {},
            }
        }

    async def _node_finalize_tailor(self, state: CareerAgentGraphState) -> dict[str, Any]:
        self._db_from_state(state)
        return {"output": dict(state.get("tailor") or {})}

    async def _node_finalize_quick_apply(self, state: CareerAgentGraphState) -> dict[str, Any]:
        self._db_from_state(state)
        return {"output": dict(state.get("application") or {})}

    async def _node_finalize_interview(self, state: CareerAgentGraphState) -> dict[str, Any]:
        self._db_from_state(state)
        return {"output": dict(state.get("interview_prep") or {})}

    async def _node_finalize_full_flow(self, state: CareerAgentGraphState) -> dict[str, Any]:
        selected_job_id = int(state.get("selected_job_id") or state.get("job_id") or 0)
        interview_prep = state.get("interview_prep") or {}
        interview_prep_link = f"/ui/prep?profile_id={state.get('profile_id')}&job_id={selected_job_id}"
        if interview_prep.get("interview_prep_id"):
            interview_prep_link += f"&prep_id={interview_prep['interview_prep_id']}"
        payload = {
            "profile_id": state.get("profile_id"),
            "query": state.get("query"),
            "selected_job": state.get("selected_job") or {},
            "matches": state.get("matches") or [],
            "source_errors": state.get("source_errors") or {},
            "tailor": state.get("tailor") or {},
            "application": state.get("application") or {},
            "interview_prep": interview_prep,
            "links": {
                "profile": f"/ui/profiles?profile_id={state.get('profile_id')}",
                "job": f"/ui/jobs?job_id={selected_job_id}",
                "resume_versions": "/ui/resumes",
                "applications": "/ui/applications",
                "interview_prep": interview_prep_link,
                "trace": "/ui/agent-runs",
            },
        }
        db = self._db_from_state(state)
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="full_career_flow", payload=payload)
        return {"output": payload}

    async def _node_completion_gate(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        request = self._request(state)
        report = self.task_contracts.verify(
            db,
            run_id=state["run_id"],
            task_type=state["task_type"],
            request=request.model_dump(),
            state=dict(state),
        )
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="completion_verification",
            payload=report,
        )
        self.trace.add_event(
            db,
            run_id=state["run_id"],
            event_type="completion_gate_passed" if report["passed"] else "completion_gate_rejected",
            node_name="completion_gate",
            payload=report,
        )
        if not report["passed"]:
            raise AgentTaskIncompleteError(format_completion_failure(report))
        output = dict(state.get("output") or {})
        output["completion_verification"] = report
        return {
            "goal_ledger": report["goal_ledger"],
            "completion_verification": report,
            "output": output,
        }

    def _route_after_plan(self, state: CareerAgentGraphState) -> str:
        return str(state["task_type"])

    def _route_after_profile(self, state: CareerAgentGraphState) -> str:
        if state["task_type"] == "find_jobs_for_profile":
            return "search_jobs"
        if state["task_type"] == "full_career_flow" and not state.get("job_id"):
            return "search_jobs"
        return "load_job"

    def _route_after_match_jobs(self, state: CareerAgentGraphState) -> str:
        if state["task_type"] == "find_jobs_for_profile":
            return "finalize_find_jobs"
        return "select_job"

    def _route_after_match_job(self, state: CareerAgentGraphState) -> str:
        task_type = state["task_type"]
        if task_type in {"tailor_resume_for_job", "full_career_flow"}:
            return "tailor_resume"
        if task_type == "quick_apply":
            return "fit_gate"
        if task_type == "prepare_interview_for_job":
            return "generate_interview_prep"
        raise ValueError(f"Unsupported task_type after match_job: {task_type}")

    def _route_after_tailor(self, state: CareerAgentGraphState) -> str:
        return "fit_gate" if state["task_type"] == "full_career_flow" else "finalize_tailor"

    def _route_after_application(self, state: CareerAgentGraphState) -> str:
        return "generate_interview_prep" if state["task_type"] == "full_career_flow" else "finalize_quick_apply"

    def _route_after_interview(self, state: CareerAgentGraphState) -> str:
        return "finalize_full_flow" if state["task_type"] == "full_career_flow" else "finalize_interview"

    def _db_from_state(self, state: CareerAgentGraphState) -> Session:
        db = self._runtime_dbs.get(int(state["run_id"]))
        if db is None:
            raise RuntimeError("LangGraph node state is missing the active database session.")
        self._raise_if_cancelled(db, int(state["run_id"]))
        return db

    def _application_confirmation(
        self,
        state: CareerAgentGraphState,
        job: Job,
        resume_version: ResumeVersion,
    ) -> dict[str, Any]:
        db = self._db_from_state(state)
        summary = {
            "job_id": job.id,
            "job_title": job.title,
            "company": job.company,
            "resume_version_id": resume_version.id,
            "fit_gate": state.get("fit_gate") or {},
        }
        approval = self.approvals.get_or_create_pending(
            db,
            run_id=state["run_id"],
            action_type="application_packet",
            payload_summary=summary,
        )
        if state.get("application_confirmed"):
            self.approvals.decide(
                db,
                approval=approval,
                approved=True,
                note="request.application_confirmed",
            )
            return {
                "confirmed": True,
                "source": "request.application_confirmed",
                "message": "调用方已显式确认生成投递包。",
                "approval_id": approval.id,
            }
        value = interrupt(
            {
                "kind": "application_packet_confirmation",
                "message": "生成投递包前需要用户确认。系统只准备材料和链接，不会自动提交最终申请。",
                "job_id": job.id,
                "job_title": job.title,
                "company": job.company,
                "resume_version_id": resume_version.id,
                "approval_id": approval.id,
                "fit_gate": state.get("fit_gate") or {},
                "required_action": "confirm_before_application_packet",
            }
        )
        if isinstance(value, dict):
            confirmed = bool(value.get("confirmed"))
            self.approvals.decide(
                db,
                approval=approval,
                approved=confirmed,
                note=value.get("note"),
                decided_by_user_id=value.get("decided_by_user_id"),
            )
            return {
                "confirmed": confirmed,
                "source": value.get("source") or "langgraph_resume",
                "note": value.get("note"),
                "resume_payload": value,
                "approval_id": approval.id,
            }
        confirmed = bool(value)
        self.approvals.decide(db, approval=approval, approved=confirmed)
        return {"confirmed": confirmed, "source": "langgraph_resume", "resume_payload": value, "approval_id": approval.id}

    def _interrupt_payloads(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"id": item.id, "value": item.value}
            for item in state.get("__interrupt__", []) or []
        ]

    def _interrupt_payloads_from_exception(self, exc: GraphInterrupt) -> list[dict[str, Any]]:
        return [
            {"id": item.id, "value": item.value}
            for item in getattr(exc, "interrupts", []) or []
        ]

    def _confirmation_output(
        self,
        run: AgentRun,
        *,
        interrupts: list[dict[str, Any]],
        graph_thread_id: str,
        execution_plan: dict[str, Any],
    ) -> dict[str, Any]:
        first_value = interrupts[0].get("value") if interrupts else {}
        interrupt_kind = first_value.get("kind") if isinstance(first_value, dict) else None
        confirmation_type = "job_selection" if interrupt_kind == "job_selection" else "application_packet"
        return {
            "requires_confirmation": True,
            "requires_input": True,
            "confirmation_type": confirmation_type,
            "interrupts": interrupts,
            "graph_thread_id": graph_thread_id,
            "execution_plan": execution_plan,
            "orchestration_framework": "langgraph",
            "resume_api": f"/agent/runs/{run.id}/resume",
        }

    def _graph_thread_id_from_run(self, run: AgentRun) -> str:
        input_json = run.input_json or {}
        output_json = run.output_json or {}
        plan = output_json.get("execution_plan") or {}
        graph_thread_id = (
            input_json.get("graph_thread_id")
            or output_json.get("graph_thread_id")
            or plan.get("graph_thread_id")
        )
        if not graph_thread_id:
            raise ValueError(f"Agent run {run.id} does not have a graph_thread_id.")
        return str(graph_thread_id)

    async def _load_profile(self, db: Session, profile_id: int | None) -> Profile:
        if profile_id is None:
            raise ValueError("profile_id is required.")
        profile = db.query(Profile).filter(Profile.id == profile_id).first()
        if profile is None:
            raise ValueError(f"Profile {profile_id} not found.")
        return profile

    async def _load_job(self, db: Session, job_id: int | None) -> Job:
        if job_id is None:
            raise ValueError("job_id is required.")
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            raise ValueError(f"Job {job_id} not found.")
        return job

    async def _async_value(self, value):
        return value

    def _fit_gate(
        self,
        db: Session,
        profile: Profile,
        job: Job,
        *,
        state: CareerAgentGraphState,
    ) -> dict[str, Any]:
        match = None
        if state.get("match_result_id"):
            match = db.query(MatchResult).filter(MatchResult.id == int(state["match_result_id"])).first()
        if match is None:
            match = self._create_match_result(
                db,
                profile,
                job,
                idempotency_key=self._idempotency_key(state, "match_fit_gate", profile.id, job.id),
            )
        payload = {
            "match_result_id": match.id,
            "overall_score": match.overall_score,
            "matched_skills": match.matched_skills_json,
            "missing_skills": match.missing_skills_json,
            "passed": match.overall_score >= 55,
            "min_score": 55,
        }
        if not payload["passed"]:
            raise ValueError(
                f"Fit gate blocked quick_apply: score {match.overall_score} is below 55. "
                f"Missing skills: {', '.join(match.missing_skills_json[:6])}"
            )
        return payload

    def _application_payload(self, application: Application) -> dict[str, Any]:
        automation_result = application.automation_result_json or {}
        return {
            "application_id": application.id,
            "profile_id": application.profile_id,
            "job_id": application.job_id,
            "resume_version_id": application.resume_version_id,
            "status": application.status,
            "apply_url": application.apply_url,
            "checklist": application.checklist_json,
            "packet_validation": automation_result.get("packet_validation"),
            "automation_result": automation_result,
            "idempotency_key": application.idempotency_key,
        }

    def _interview_prep_payload(self, prep) -> dict[str, Any]:
        return {
            "interview_prep_id": prep.id,
            "profile_id": prep.profile_id,
            "job_id": prep.job_id,
            "match_result_id": prep.match_result_id,
            "title": prep.title,
            "summary": prep.summary_json,
            "coverage": prep.coverage_json,
            "question_set_count": len(prep.question_sets_json or []),
            "gap_drill_count": len(prep.gap_drills_json or []),
            "research_item_count": len(prep.research_checklist_json or []),
            "idempotency_key": getattr(prep, "idempotency_key", None),
        }

    def _tailor_payload(
        self,
        state: CareerAgentGraphState,
        profile: Profile,
        job: Job,
        version: ResumeVersion,
        *,
        idempotency_reused: bool,
    ) -> dict[str, Any]:
        payload = {
            "profile_id": profile.id,
            "job_id": job.id,
            "match_result_id": state.get("match_result_id"),
            "overall_score": state.get("overall_score"),
            "resume_version_id": version.id,
            "verification": version.verification_json,
            "idempotency_key": version.idempotency_key,
            "idempotency_reused": idempotency_reused,
        }
        return {"resume_version_id": version.id, "verification": version.verification_json, "tailor": payload}

    def _idempotency_key(self, state: CareerAgentGraphState, kind: str, *parts: object) -> str:
        return ":".join(["agent_run", str(state["run_id"]), kind, *(str(part) for part in parts)])

    def _create_match_result(
        self,
        db: Session,
        profile: Profile,
        job: Job,
        *,
        idempotency_key: str,
    ) -> MatchResult:
        kwargs = self._supported_kwargs(
            self.matcher.create_match_result,
            idempotency_key=idempotency_key,
        )
        result = self.matcher.create_match_result(db, profile, job, **kwargs)
        if getattr(result, "idempotency_key", None) is None:
            self._assign_idempotency_key(db, result, idempotency_key)
        return result

    async def _tailor_resume_with_idempotency(
        self,
        db: Session,
        profile: Profile,
        job: Job,
        idempotency_key: str,
    ) -> ResumeVersion:
        kwargs = self._supported_kwargs(
            self.tailor.tailor_resume,
            idempotency_key=idempotency_key,
        )
        return await self.tailor.tailor_resume(db, profile, job, **kwargs)

    @staticmethod
    def _supported_kwargs(callable_obj, **kwargs: Any) -> dict[str, Any]:
        try:
            parameters = inspect.signature(callable_obj).parameters
        except (TypeError, ValueError):
            return {}
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
            return kwargs
        return {key: value for key, value in kwargs.items() if key in parameters}

    def _resume_by_idempotency_key(self, db: Session, key: str) -> ResumeVersion | None:
        return db.query(ResumeVersion).filter(ResumeVersion.idempotency_key == key).first()

    def _application_by_idempotency_key(self, db: Session, key: str) -> Application | None:
        return db.query(Application).filter(Application.idempotency_key == key).first()

    def _interview_prep_by_idempotency_key(self, db: Session, key: str):
        from app.models.entities import InterviewPrep

        return db.query(InterviewPrep).filter(InterviewPrep.idempotency_key == key).first()

    def _assign_idempotency_key(self, db: Session, row: Any, key: str) -> None:
        if getattr(row, "idempotency_key", None) == key:
            return
        setattr(row, "idempotency_key", key)
        db.add(row)
        db.commit()
        db.refresh(row)

    def _record_idempotency_reuse(
        self,
        db: Session,
        run_id: int,
        artifact_type: str,
        key: str,
        existing_id: int,
    ) -> None:
        self.trace.add_event(
            db,
            run_id=run_id,
            event_type="idempotency_reused",
            payload={"artifact_type": artifact_type, "idempotency_key": key, "existing_id": existing_id},
            node_name=artifact_type,
        )

    def _raise_if_cancelled(self, db: Session, run_id: int) -> None:
        try:
            db.expire_all()
        except Exception:
            pass
        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        redis_cancelled = self._redis_cancel_requested(run_id)
        if run is not None and (run.status in {"cancelled", "withdrawn"} or redis_cancelled):
            raise AgentRunCancelled(f"Agent run {run_id} was cancelled.")

    def _set_redis_cancel_flag(self, run_id: int) -> None:
        try:
            get_redis_client().set(
                redis_key("career_agent", "runs", "cancel", run_id),
                "1",
                ex=self.settings.redis_run_lock_ttl_seconds,
            )
        except RedisUnavailableError:
            return

    def _redis_cancel_requested(self, run_id: int) -> bool:
        try:
            return bool(get_redis_client().get(redis_key("career_agent", "runs", "cancel", run_id)))
        except RedisUnavailableError:
            return False
