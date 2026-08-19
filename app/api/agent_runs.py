import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agents.orchestrator import AgentOrchestrator
from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.core.redis_client import RedisUnavailableError, get_redis_client, redis_key
from app.core.security import AuthContext, has_admin_access, optional_auth_context
from app.models.entities import AgentApproval, AgentEvent, AgentRun, AgentRunControlAction, AgentStep, Job, Profile
from app.models.schemas import (
    AgentDirectiveRequest,
    AgentDirectiveResponse,
    AgentApprovalResponse,
    AgentCheckpointResponse,
    AgentEventResponse,
    AgentRunCancelRequest,
    AgentRunControlActionResponse,
    AgentRunRequest,
    AgentRunRewindRequest,
    AgentRunResponse,
    AgentRunResumeRequest,
    AgentRunWithdrawRequest,
    AgentStepResponse,
)
from app.services.agent_directives import AgentDirectiveService
from app.services.run_control import ACTIVE_RUN_STATUSES, RunControlService, RunWithdrawalConflict
from app.services.trace_service import TraceService
from app.services.run_business_summary import RunBusinessSummaryService
from app.services.task_runner import get_task_runner

router = APIRouter(prefix="/agent/runs", tags=["agent-runs"])


def _tenant_query(query, auth: AuthContext):
    if get_settings().rbac_enabled:
        query = query.filter(AgentRun.tenant_id == auth.tenant_id)
        if not has_admin_access(auth):
            query = query.filter(or_(AgentRun.user_id == auth.user_id, AgentRun.user_id.is_(None)))
    return query


def _set_run_tenant(run: AgentRun, auth: AuthContext, db: Session) -> AgentRun:
    if get_settings().rbac_enabled:
        run.tenant_id = auth.tenant_id
        db.add(run)
        db.commit()
        db.refresh(run)
    return run


def _tenant_run_or_404(db: Session, run_id: int, auth: AuthContext) -> AgentRun:
    run = _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return run


def _validate_resource_scope(db: Session, payload: AgentRunRequest, auth: AuthContext) -> None:
    if not get_settings().rbac_enabled:
        return
    if payload.profile_id is not None:
        profile = db.query(Profile).filter(Profile.id == payload.profile_id).first()
        if profile is None or profile.tenant_id != auth.tenant_id:
            raise HTTPException(status_code=404, detail="Profile not found.")
    if payload.job_id is not None:
        job = db.query(Job).filter(Job.id == payload.job_id).first()
        if job is None or job.tenant_id not in {None, auth.tenant_id}:
            raise HTTPException(status_code=404, detail="Job not found.")


@router.post("", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_run(
    payload: AgentRunRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> AgentRunResponse:
    _validate_resource_scope(db, payload, auth)
    _enforce_active_run_limit(db, payload, auth=auth)
    run = await AgentOrchestrator().run(
        db,
        payload,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
    )
    return AgentRunResponse.model_validate(_set_run_tenant(run, auth, db))


@router.post("/background", response_model=AgentRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_background_agent_run(
    payload: AgentRunRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> AgentRunResponse:
    _validate_resource_scope(db, payload, auth)
    _enforce_active_run_limit(db, payload, auth=auth)
    run = AgentOrchestrator().queue_run(
        db,
        payload,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
    )
    _set_run_tenant(run, auth, db)
    try:
        get_task_runner().enqueue_agent_run(run.id)
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error_message = f"Queue enqueue failed: {exc}"
        run.output_json = {"error_type": "queue_unavailable", "error": str(exc)}
        db.add(run)
        db.commit()
        db.refresh(run)
        trace = TraceService()
        trace.add_event(
            db,
            run_id=run.id,
            event_type="queue_enqueue_failed",
            node_name="queue",
            payload={"error_type": "queue_unavailable", "error": str(exc)},
        )
        trace.add_event(
            db,
            run_id=run.id,
            event_type="run_finished",
            payload={"status": "failed", "error_message": run.error_message, "output_json": run.output_json},
        )
    return AgentRunResponse.model_validate(run)


@router.post("/{run_id}/resume", response_model=AgentRunResponse)
async def resume_agent_run(
    run_id: int,
    payload: AgentRunResumeRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> AgentRunResponse:
    if _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first() is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    resume_payload = {**payload.resume_json, "confirmed": payload.confirmed, "note": payload.note}
    try:
        run = await AgentOrchestrator().resume(db, run_id, resume_payload)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return AgentRunResponse.model_validate(run)


@router.post("/{run_id}/cancel", response_model=AgentRunResponse)
async def cancel_agent_run(
    run_id: int,
    payload: AgentRunCancelRequest | None = None,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> AgentRunResponse:
    if _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first() is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    try:
        run = AgentOrchestrator().cancel(db, run_id, reason=(payload.reason if payload else None))
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return AgentRunResponse.model_validate(run)


@router.post(
    "/{run_id}/directives",
    response_model=AgentDirectiveResponse,
    status_code=status.HTTP_201_CREATED,
)
async def append_agent_run_directive(
    run_id: int,
    payload: AgentDirectiveRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> AgentDirectiveResponse:
    run = _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    try:
        directive = await AgentDirectiveService().append(db, source_run=run, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentDirectiveResponse.model_validate(directive)


@router.get("/{run_id}/directives", response_model=list[AgentDirectiveResponse])
def list_agent_run_directives(
    run_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> list[AgentDirectiveResponse]:
    if _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first() is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    rows = AgentDirectiveService().list_for_run(db, source_run_id=run_id, limit=limit)
    return [AgentDirectiveResponse.model_validate(row) for row in rows]


@router.get("/{run_id}/checkpoints", response_model=list[AgentCheckpointResponse])
async def list_agent_run_checkpoints(
    run_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> list[AgentCheckpointResponse]:
    run = _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    try:
        rows = await AgentOrchestrator().checkpoint_history(run, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return [AgentCheckpointResponse.model_validate(row) for row in rows]


@router.post("/{run_id}/checkpoints/{checkpoint_id}/rewind", response_model=AgentRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def rewind_agent_run_checkpoint(
    run_id: int,
    checkpoint_id: str,
    payload: AgentRunRewindRequest | None = None,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> AgentRunResponse:
    if _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first() is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    try:
        fork_run = await AgentOrchestrator().rewind_from_checkpoint(
            db,
            run_id=run_id,
            checkpoint_id=checkpoint_id,
            actor=auth.actor,
            reason=payload.reason if payload else None,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        get_task_runner().enqueue_agent_run(fork_run.id)
    except RedisUnavailableError as exc:
        fork_run.status = "failed"
        fork_run.error_message = f"Checkpoint rewind queue enqueue failed: {exc}"
        db.add(fork_run)
        db.commit()
        db.refresh(fork_run)
        TraceService().add_event(
            db,
            run_id=fork_run.id,
            event_type="checkpoint_rewind_enqueue_failed",
            payload={"error": str(exc)},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AgentRunResponse.model_validate(fork_run)


@router.get("/{run_id}/withdrawal-preview")
def get_agent_run_withdrawal_preview(
    run_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> dict:
    run = _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return RunControlService().withdrawal_preview(db, run)


@router.post("/{run_id}/withdraw")
def withdraw_agent_run(
    run_id: int,
    payload: AgentRunWithdrawRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> dict:
    run = _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    try:
        if run.status in ACTIVE_RUN_STATUSES:
            run = AgentOrchestrator().cancel(db, run.id, reason=f"撤回前取消：{payload.reason}")
        run, action = RunControlService().withdraw(
            db,
            run=run,
            reason=payload.reason,
            actor=auth.actor,
        )
    except RunWithdrawalConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "irreversible_actions": exc.irreversible_actions},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "run": AgentRunResponse.model_validate(run).model_dump(mode="json"),
        "control_action": AgentRunControlActionResponse.model_validate(action).model_dump(mode="json"),
    }


@router.get("/{run_id}/control-actions", response_model=list[AgentRunControlActionResponse])
def list_agent_run_control_actions(
    run_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> list[AgentRunControlActionResponse]:
    if _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first() is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    rows = (
        db.query(AgentRunControlAction)
        .filter(AgentRunControlAction.run_id == run_id)
        .order_by(AgentRunControlAction.id.desc())
        .all()
    )
    return [AgentRunControlActionResponse.model_validate(row) for row in rows]


@router.get("", response_model=list[AgentRunResponse])
def list_agent_runs(
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> list[AgentRunResponse]:
    rows = (
        _tenant_query(db.query(AgentRun), auth)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(limit)
        .all()
    )
    return [AgentRunResponse.model_validate(row) for row in rows]


@router.get("/{run_id}", response_model=AgentRunResponse)
def get_agent_run(run_id: int, db: Session = Depends(get_db), auth: AuthContext = Depends(optional_auth_context)) -> AgentRunResponse:
    run = _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return AgentRunResponse.model_validate(run)


@router.get("/{run_id}/summary")
def get_agent_run_summary(
    run_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> dict:
    run = _tenant_query(db.query(AgentRun), auth).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return RunBusinessSummaryService().build(db, run=run)


@router.get("/{run_id}/graph-state")
async def get_agent_graph_state(
    run_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> dict:
    run = _tenant_run_or_404(db, run_id, auth)
    try:
        return await AgentOrchestrator().graph_state(run)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{run_id}/steps", response_model=list[AgentStepResponse])
def get_agent_steps(
    run_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> list[AgentStepResponse]:
    _tenant_run_or_404(db, run_id, auth)
    rows = db.query(AgentStep).filter(AgentStep.run_id == run_id).order_by(AgentStep.id.asc()).all()
    return [AgentStepResponse.model_validate(row) for row in rows]


@router.get("/{run_id}/approvals", response_model=list[AgentApprovalResponse])
def get_agent_approvals(
    run_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> list[AgentApprovalResponse]:
    _tenant_run_or_404(db, run_id, auth)
    rows = (
        db.query(AgentApproval)
        .filter(AgentApproval.run_id == run_id)
        .order_by(AgentApproval.id.asc())
        .all()
    )
    return [AgentApprovalResponse.model_validate(row) for row in rows]


@router.get("/{run_id}/events", response_model=list[AgentEventResponse])
def get_agent_events(
    run_id: int,
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> list[AgentEventResponse]:
    _tenant_run_or_404(db, run_id, auth)
    rows = (
        db.query(AgentEvent)
        .filter(AgentEvent.run_id == run_id, AgentEvent.id > after_id)
        .order_by(AgentEvent.id.asc())
        .limit(limit)
        .all()
    )
    return [AgentEventResponse.model_validate(row) for row in rows]


@router.get("/{run_id}/events/stream")
def stream_agent_events(
    run_id: int,
    after_id: int = Query(default=0, ge=0),
    heartbeat_seconds: float = Query(default=1.0, ge=0.2, le=10.0),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> StreamingResponse:
    _tenant_run_or_404(db, run_id, auth)
    return StreamingResponse(
        _agent_event_sse(
            run_id,
            after_id=after_id,
            heartbeat_seconds=heartbeat_seconds,
            tenant_id=auth.tenant_id if get_settings().rbac_enabled else None,
            user_id=(
                None
                if not get_settings().rbac_enabled or has_admin_access(auth)
                else auth.user_id
            ),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _agent_event_sse(
    run_id: int,
    *,
    after_id: int,
    heartbeat_seconds: float,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> AsyncIterator[str]:
    last_id = after_id
    final_statuses = {"completed", "failed", "waiting_for_confirmation", "cancelled", "withdrawn"}
    while True:
        db = SessionLocal()
        try:
            query = db.query(AgentRun).filter(AgentRun.id == run_id)
            if tenant_id is not None:
                query = query.filter(AgentRun.tenant_id == tenant_id)
            if user_id is not None:
                query = query.filter(or_(AgentRun.user_id == user_id, AgentRun.user_id.is_(None)))
            run = query.first()
            if run is None:
                yield _sse("error", {"detail": "Agent run not found.", "run_id": run_id})
                return
            rows = (
                db.query(AgentEvent)
                .filter(AgentEvent.run_id == run_id, AgentEvent.id > last_id)
                .order_by(AgentEvent.id.asc())
                .limit(100)
                .all()
            )
            for row in rows:
                last_id = row.id
                yield _sse(
                    row.event_type,
                    {
                        "id": row.id,
                        "run_id": row.run_id,
                        "event_type": row.event_type,
                        "node_name": row.node_name,
                        "event_json": row.event_json,
                        "created_at": row.created_at.isoformat(),
                    },
                    event_id=row.id,
                )
            if run.status in final_statuses and not rows:
                yield _sse(
                    "run_closed",
                    {
                        "run_id": run.id,
                        "status": run.status,
                        "output_json": run.output_json or {},
                        "error_message": run.error_message,
                    },
                )
                return
        finally:
            db.close()
        yield _sse("heartbeat", {"run_id": run_id, "after_id": last_id})
        await asyncio.sleep(heartbeat_seconds)


def _sse(event: str, data: dict, *, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def _enforce_active_run_limit(db: Session, payload: AgentRunRequest, *, auth: AuthContext) -> None:
    if payload.profile_id is None:
        return
    settings = get_settings()
    if settings.redis_enabled:
        try:
            redis = get_redis_client()
            key = redis_key("career_agent", "rate", "profile", payload.profile_id)
            current = int(redis.incr(key))
            if current == 1:
                redis.expire(key, settings.redis_rate_limit_window_seconds)
            if current > settings.redis_rate_limit_max_runs:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Profile {payload.profile_id} exceeded Redis rate limit: "
                        f"{settings.redis_rate_limit_max_runs} runs per "
                        f"{settings.redis_rate_limit_window_seconds}s."
                    ),
                )
        except RedisUnavailableError as exc:
            raise HTTPException(status_code=503, detail=f"Redis rate limiter unavailable: {exc}") from exc
    active_statuses = {"queued", "running", "waiting_for_confirmation"}
    active_query = db.query(AgentRun).filter(
        AgentRun.profile_id == payload.profile_id,
        AgentRun.status.in_(active_statuses),
    )
    if settings.rbac_enabled:
        active_query = active_query.filter(AgentRun.tenant_id == auth.tenant_id)
    active_count = active_query.count()
    if active_count >= settings.agent_active_run_limit_per_profile:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Profile {payload.profile_id} already has {active_count} active Agent runs; "
                f"limit is {settings.agent_active_run_limit_per_profile}."
            ),
        )
    if payload.task_type == "full_career_flow" and payload.job_id is not None:
        duplicate_query = db.query(AgentRun).filter(
            AgentRun.profile_id == payload.profile_id,
            AgentRun.job_id == payload.job_id,
            AgentRun.task_type == "full_career_flow",
            AgentRun.status.in_(active_statuses),
        )
        if settings.rbac_enabled:
            duplicate_query = duplicate_query.filter(AgentRun.tenant_id == auth.tenant_id)
        duplicate = duplicate_query.first()
        if duplicate is not None:
            raise HTTPException(
                status_code=429,
                detail=f"Full career flow for profile {payload.profile_id} and job {payload.job_id} is already active.",
            )
