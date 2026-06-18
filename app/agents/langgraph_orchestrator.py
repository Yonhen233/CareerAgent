import time
from typing import Any, Literal, TypedDict
from uuid import uuid4

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.orm import Session

from app.agents.tools import AgentPlanner
from app.core.config import get_settings
from app.models.entities import AgentRun
from app.models.entities import Application, Job, Profile, ResumeVersion
from app.models.schemas import AgentRunRequest
from app.services.application_service import ApplicationService
from app.services.interview_prep import InterviewPrepService
from app.services.job_search import JobSearchService
from app.services.matcher import MatcherService
from app.services.resume_tailor import ResumeTailorService
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
    ) -> None:
        self.trace = trace or TraceService()
        self.job_search = job_search or JobSearchService()
        self.matcher = matcher or MatcherService()
        self.tailor = tailor or ResumeTailorService()
        self.application = application or ApplicationService()
        self.interview_prep = interview_prep or InterviewPrepService()
        self.planner = planner or AgentPlanner()
        self.settings = get_settings()
        self._runtime_dbs: dict[int, Session] = {}
        self._runtime_plans: dict[int, dict[str, Any]] = {}
        self._checkpoint_conn = None
        self.checkpointer = None
        self._graph = None

    async def run(self, db: Session, request: AgentRunRequest):
        started = time.perf_counter()
        graph_thread_id = f"agent-run-{uuid4().hex}"
        run = self.trace.create_run(
            db,
            task_type=request.task_type,
            profile_id=request.profile_id,
            job_id=request.job_id,
            input_json={
                **request.model_dump(),
                "orchestration_framework": "langgraph",
                "graph_thread_id": graph_thread_id,
            },
        )
        return await self._execute_run(db, run, request, graph_thread_id, started)

    def queue_run(self, db: Session, request: AgentRunRequest) -> AgentRun:
        graph_thread_id = f"agent-run-{uuid4().hex}"
        return self.trace.create_run(
            db,
            task_type=request.task_type,
            profile_id=request.profile_id,
            job_id=request.job_id,
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
        return await self._execute_run(db, run, request, graph_thread_id, started)

    async def _execute_run(
        self,
        db: Session,
        run: AgentRun,
        request: AgentRunRequest,
        graph_thread_id: str,
        started: float,
    ) -> AgentRun:
        self._runtime_dbs[run.id] = db
        run.status = "running"
        db.add(run)
        db.commit()
        self.trace.add_event(
            db,
            run_id=run.id,
            event_type="run_started",
            payload={"task_type": request.task_type, "graph_thread_id": graph_thread_id},
        )
        try:
            graph = await self._ensure_graph()
            final_state = await self._invoke_graph(
                graph,
                {
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
                },
                db=db,
                run_id=run.id,
                config={"configurable": {"thread_id": graph_thread_id}},
            )
            interrupts = self._interrupt_payloads(final_state)
            if interrupts:
                output = {
                    "requires_confirmation": True,
                    "confirmation_type": "application_packet",
                    "interrupts": interrupts,
                    "graph_thread_id": graph_thread_id,
                    "execution_plan": final_state.get("execution_plan") or {},
                    "orchestration_framework": "langgraph",
                    "resume_api": f"/agent/runs/{run.id}/resume",
                }
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
            return self.trace.finish_run(db, run=run, status="completed", output_json=output, started_at=started)
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
            interrupts = self._interrupt_payloads(final_state)
            if interrupts:
                output = {
                    "requires_confirmation": True,
                    "confirmation_type": "application_packet",
                    "interrupts": interrupts,
                    "graph_thread_id": graph_thread_id,
                    "execution_plan": final_state.get("execution_plan") or self._runtime_plans.get(run.id) or {},
                    "orchestration_framework": "langgraph",
                    "resume_api": f"/agent/runs/{run.id}/resume",
                }
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
                started_at=started,
            )
        finally:
            self._runtime_dbs.pop(run.id, None)
            self._runtime_plans.pop(run.id, None)
            await self._close_checkpoint()

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

    async def _invoke_graph(
        self,
        graph,
        payload: dict[str, Any] | Command,
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
            self.trace.add_event(
                db,
                run_id=run_id,
                event_type="graph_failed",
                payload={"error": str(exc), "error_type": exc.__class__.__name__},
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
        graph.add_edge("finalize_find_jobs", END)
        graph.add_edge("finalize_tailor", END)
        graph.add_edge("finalize_quick_apply", END)
        graph.add_edge("finalize_interview", END)
        graph.add_edge("finalize_full_flow", END)
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

    def _request(self, state: CareerAgentGraphState) -> AgentRunRequest:
        return AgentRunRequest(**state["request"])

    async def _node_plan_task(self, state: CareerAgentGraphState) -> dict[str, Any]:
        request = self._request(state)
        db = self._db_from_state(state)
        plan = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="plan_task",
            tool_name="LangGraph.AgentPlanner",
            input_json={"task_type": request.task_type},
            handler=lambda: self._async_value(
                {
                    **self.planner.build_plan(request),
                    "orchestration_framework": "langgraph",
                    "graph_thread_id": state.get("graph_thread_id"),
                    "checkpoint_backend": "sqlite",
                    "interrupt_policy": "quick_apply_requires_application_confirmation",
                }
            ),
        )
        self._runtime_plans[state["run_id"]] = plan
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="execution_plan", payload=plan)
        return {"execution_plan": plan}

    async def _node_load_profile(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="load_profile",
            tool_name="ProfileRepository",
            input_json={"profile_id": state.get("profile_id")},
            handler=lambda: self._load_profile(db, state.get("profile_id")),
        )
        query = state.get("query") or " ".join(profile.target_roles_json or []) or "Agent 开发实习生"
        return {"profile_id": profile.id, "query": query}

    async def _node_search_jobs(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        jobs, source_errors = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="search_jobs",
            tool_name="JobSearchService",
            input_json={"query": state.get("query"), "location": state.get("location"), "limit": state.get("limit")},
            handler=lambda: self.job_search.search(
                db,
                query=state.get("query") or "Agent 开发实习生",
                location=state.get("location"),
                internship_only=True,
                limit=int(state.get("limit") or 20),
                store_results=True,
            ),
        )
        return {"job_ids": [job.id for job in jobs], "source_errors": source_errors}

    async def _node_match_jobs(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        matches: list[dict[str, Any]] = []
        for job_id in state.get("job_ids", []):
            job = await self._load_job(db, int(job_id))
            match = await self.trace.step(
                db,
                run_id=state["run_id"],
                step_name=f"match_job_{job.id}",
                tool_name="MatcherService",
                input_json={"profile_id": profile.id, "job_id": job.id},
                handler=lambda job=job: self._async_value(self.matcher.create_match_result(db, profile, job)),
            )
            matches.append(
                {
                    "job_id": job.id,
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
        selected_job = dict(matches[0])
        db = self._db_from_state(state)
        self.trace.add_artifact(
            db,
            run_id=state["run_id"],
            artifact_type="selected_job",
            payload={"selection_policy": "highest_overall_score", "selected_job": selected_job},
        )
        return {"selected_job": selected_job, "job_id": int(selected_job["job_id"]), "selected_job_id": int(selected_job["job_id"])}

    async def _node_load_job(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        job = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="load_job",
            tool_name="JobRepository",
            input_json={"job_id": state.get("job_id")},
            handler=lambda: self._load_job(db, state.get("job_id")),
        )
        return {"job_id": job.id}

    async def _node_match_job(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        job = await self._load_job(db, state.get("job_id"))
        match = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="match_job",
            tool_name="MatcherService",
            input_json={"profile_id": profile.id, "job_id": job.id},
            handler=lambda: self._async_value(self.matcher.create_match_result(db, profile, job)),
        )
        payload = {
            "match_result_id": match.id,
            "overall_score": match.overall_score,
            "matched_skills": match.matched_skills_json,
            "missing_skills": match.missing_skills_json,
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
        version = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="tailor_resume_with_rag",
            tool_name="ResumeTailorService",
            input_json={"profile_id": profile.id, "job_id": job.id},
            handler=lambda: self.tailor.tailor_resume(db, profile, job),
        )
        payload = {
            "profile_id": profile.id,
            "job_id": job.id,
            "match_result_id": state.get("match_result_id"),
            "overall_score": state.get("overall_score"),
            "resume_version_id": version.id,
            "verification": version.verification_json,
        }
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
            tool_name="MatcherService",
            input_json={"profile_id": profile.id, "job_id": job.id, "min_score": 55},
            handler=lambda: self._async_value(self._fit_gate(db, profile, job)),
        )
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="fit_gate", payload=fit_gate)
        return {"fit_gate": fit_gate}

    async def _node_ensure_resume_version(self, state: CareerAgentGraphState) -> dict[str, Any]:
        if state.get("resume_version_id"):
            return {"resume_version_id": int(state["resume_version_id"])}
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        job = await self._load_job(db, state.get("job_id"))
        version = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="create_missing_tailored_resume",
            tool_name="ResumeTailorService",
            input_json={"profile_id": profile.id, "job_id": job.id},
            handler=lambda: self.tailor.tailor_resume(db, profile, job),
        )
        return {"resume_version_id": version.id}

    async def _node_create_application_packet(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        job = await self._load_job(db, state.get("job_id"))
        resume_version = db.query(ResumeVersion).filter(ResumeVersion.id == state.get("resume_version_id")).first()
        if resume_version is None:
            raise ValueError(f"ResumeVersion {state.get('resume_version_id')} not found.")
        confirmation = self._application_confirmation(state, job, resume_version)
        if not confirmation.get("confirmed"):
            raise ValueError("Application confirmation rejected by user.")
        application = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="create_application_packet",
            tool_name="ApplicationService",
            input_json={"profile_id": profile.id, "job_id": job.id, "resume_version_id": resume_version.id},
            handler=lambda: self.application.create_quick_apply_packet(
                db,
                profile=profile,
                job=job,
                resume_version=resume_version,
                browser_assist=False,
            ),
        )
        payload = self._application_payload(application)
        payload["fit_gate"] = state.get("fit_gate")
        payload["human_confirmation"] = confirmation
        return {"application": payload}

    async def _node_generate_interview_prep(self, state: CareerAgentGraphState) -> dict[str, Any]:
        db = self._db_from_state(state)
        profile = await self._load_profile(db, state.get("profile_id"))
        job = await self._load_job(db, state.get("job_id"))
        match_result = self.matcher.create_match_result(db, profile, job)
        prep = await self.trace.step(
            db,
            run_id=state["run_id"],
            step_name="generate_interview_prep",
            tool_name="InterviewPrepService",
            input_json={"profile_id": profile.id, "job_id": job.id, "match_result_id": match_result.id},
            handler=lambda: self.interview_prep.create_interview_prep_with_llm(
                db, profile=profile, job=job, match_result=match_result
            ),
        )
        payload = self._interview_prep_payload(prep)
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="interview_prep", payload=payload)
        return {"interview_prep": payload}

    async def _node_finalize_find_jobs(self, state: CareerAgentGraphState) -> dict[str, Any]:
        return {
            "output": {
                "profile_id": state.get("profile_id"),
                "query": state.get("query"),
                "matches": state.get("matches") or [],
                "source_errors": state.get("source_errors") or {},
            }
        }

    async def _node_finalize_tailor(self, state: CareerAgentGraphState) -> dict[str, Any]:
        return {"output": dict(state.get("tailor") or {})}

    async def _node_finalize_quick_apply(self, state: CareerAgentGraphState) -> dict[str, Any]:
        return {"output": dict(state.get("application") or {})}

    async def _node_finalize_interview(self, state: CareerAgentGraphState) -> dict[str, Any]:
        return {"output": dict(state.get("interview_prep") or {})}

    async def _node_finalize_full_flow(self, state: CareerAgentGraphState) -> dict[str, Any]:
        selected_job_id = int(state.get("selected_job_id") or state.get("job_id") or 0)
        payload = {
            "profile_id": state.get("profile_id"),
            "query": state.get("query"),
            "selected_job": state.get("selected_job") or {},
            "matches": state.get("matches") or [],
            "source_errors": state.get("source_errors") or {},
            "tailor": state.get("tailor") or {},
            "application": state.get("application") or {},
            "interview_prep": state.get("interview_prep") or {},
            "links": {
                "profile": f"/ui/profiles?profile_id={state.get('profile_id')}",
                "job": f"/ui/jobs?job_id={selected_job_id}",
                "resume_versions": "/ui/resumes",
                "applications": "/ui/applications",
                "interview_prep": f"/ui/prep?job_id={selected_job_id}",
                "trace": "/ui/agent-runs",
            },
        }
        db = self._db_from_state(state)
        self.trace.add_artifact(db, run_id=state["run_id"], artifact_type="full_career_flow", payload=payload)
        return {"output": payload}

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
        return db

    def _application_confirmation(
        self,
        state: CareerAgentGraphState,
        job: Job,
        resume_version: ResumeVersion,
    ) -> dict[str, Any]:
        if state.get("application_confirmed"):
            return {
                "confirmed": True,
                "source": "request.application_confirmed",
                "message": "调用方已显式确认生成投递包。",
            }
        value = interrupt(
            {
                "kind": "application_packet_confirmation",
                "message": "生成投递包前需要用户确认。系统只准备材料和链接，不会自动提交最终申请。",
                "job_id": job.id,
                "job_title": job.title,
                "company": job.company,
                "resume_version_id": resume_version.id,
                "fit_gate": state.get("fit_gate") or {},
                "required_action": "confirm_before_application_packet",
            }
        )
        if isinstance(value, dict):
            return {
                "confirmed": bool(value.get("confirmed")),
                "source": value.get("source") or "langgraph_resume",
                "note": value.get("note"),
                "resume_payload": value,
            }
        return {"confirmed": bool(value), "source": "langgraph_resume", "resume_payload": value}

    def _interrupt_payloads(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"id": item.id, "value": item.value}
            for item in state.get("__interrupt__", []) or []
        ]

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

    def _fit_gate(self, db: Session, profile: Profile, job: Job) -> dict[str, Any]:
        match = self.matcher.create_match_result(db, profile, job)
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
        }
