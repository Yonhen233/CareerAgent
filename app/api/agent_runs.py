import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agents.orchestrator import AgentOrchestrator
from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.core.redis_client import RedisUnavailableError, get_redis_client, redis_key
from app.models.entities import AgentApproval, AgentEvent, AgentRun, AgentStep
from app.models.schemas import (
    AgentApprovalResponse,
    AgentEventResponse,
    AgentRunCancelRequest,
    AgentRunRequest,
    AgentRunResponse,
    AgentRunResumeRequest,
    AgentStepResponse,
)
from app.services.task_runner import get_task_runner

router = APIRouter(prefix="/agent/runs", tags=["agent-runs"])


@router.post("", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_run(payload: AgentRunRequest, db: Session = Depends(get_db)) -> AgentRunResponse:
    _enforce_active_run_limit(db, payload)
    run = await AgentOrchestrator().run(db, payload)
    return AgentRunResponse.model_validate(run)


@router.post("/background", response_model=AgentRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_background_agent_run(
    payload: AgentRunRequest,
    db: Session = Depends(get_db),
) -> AgentRunResponse:
    _enforce_active_run_limit(db, payload)
    run = AgentOrchestrator().queue_run(db, payload)
    try:
        get_task_runner().enqueue_agent_run(run.id)
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error_message = f"Queue enqueue failed: {exc}"
        run.output_json = {"error_type": "queue_unavailable", "error": str(exc)}
        db.add(run)
        db.commit()
        raise HTTPException(status_code=503, detail=f"Agent queue unavailable: {exc}") from exc
    return AgentRunResponse.model_validate(run)


@router.post("/{run_id}/resume", response_model=AgentRunResponse)
async def resume_agent_run(
    run_id: int,
    payload: AgentRunResumeRequest,
    db: Session = Depends(get_db),
) -> AgentRunResponse:
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
) -> AgentRunResponse:
    try:
        run = AgentOrchestrator().cancel(db, run_id, reason=(payload.reason if payload else None))
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return AgentRunResponse.model_validate(run)


@router.get("", response_model=list[AgentRunResponse])
def list_agent_runs(db: Session = Depends(get_db)) -> list[AgentRunResponse]:
    rows = db.query(AgentRun).order_by(AgentRun.created_at.desc()).limit(100).all()
    return [AgentRunResponse.model_validate(row) for row in rows]


@router.get("/{run_id}", response_model=AgentRunResponse)
def get_agent_run(run_id: int, db: Session = Depends(get_db)) -> AgentRunResponse:
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return AgentRunResponse.model_validate(run)


@router.get("/{run_id}/graph-state")
async def get_agent_graph_state(run_id: int, db: Session = Depends(get_db)) -> dict:
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    try:
        return await AgentOrchestrator().graph_state(run)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{run_id}/steps", response_model=list[AgentStepResponse])
def get_agent_steps(run_id: int, db: Session = Depends(get_db)) -> list[AgentStepResponse]:
    rows = db.query(AgentStep).filter(AgentStep.run_id == run_id).order_by(AgentStep.id.asc()).all()
    return [AgentStepResponse.model_validate(row) for row in rows]


@router.get("/{run_id}/approvals", response_model=list[AgentApprovalResponse])
def get_agent_approvals(run_id: int, db: Session = Depends(get_db)) -> list[AgentApprovalResponse]:
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
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
) -> list[AgentEventResponse]:
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
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
) -> StreamingResponse:
    return StreamingResponse(
        _agent_event_sse(run_id, after_id=after_id, heartbeat_seconds=heartbeat_seconds),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _agent_event_sse(run_id: int, *, after_id: int, heartbeat_seconds: float) -> AsyncIterator[str]:
    last_id = after_id
    final_statuses = {"completed", "failed", "waiting_for_confirmation", "cancelled"}
    while True:
        db = SessionLocal()
        try:
            run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
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


def _enforce_active_run_limit(db: Session, payload: AgentRunRequest) -> None:
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
    active_count = (
        db.query(AgentRun)
        .filter(AgentRun.profile_id == payload.profile_id, AgentRun.status.in_(active_statuses))
        .count()
    )
    if active_count >= settings.agent_active_run_limit_per_profile:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Profile {payload.profile_id} already has {active_count} active Agent runs; "
                f"limit is {settings.agent_active_run_limit_per_profile}."
            ),
        )
    if payload.task_type == "full_career_flow" and payload.job_id is not None:
        duplicate = (
            db.query(AgentRun)
            .filter(
                AgentRun.profile_id == payload.profile_id,
                AgentRun.job_id == payload.job_id,
                AgentRun.task_type == "full_career_flow",
                AgentRun.status.in_(active_statuses),
            )
            .first()
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=429,
                detail=f"Full career flow for profile {payload.profile_id} and job {payload.job_id} is already active.",
            )
