from sqlalchemy import text
from sqlalchemy.orm import Session

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import get_settings
from app.core.database import engine, get_db
from app.core.redis_client import RedisUnavailableError, get_redis_client
from app.core.security import require_admin
from app.core.telemetry import telemetry
from app.models.entities import AgentApproval, AgentRun, EvaluationRun, LLMCallLog, TaskRun
from app.models.schemas import (
    AgentApprovalCreateRequest,
    AgentApprovalDecisionRequest,
    AgentApprovalResponse,
    AgentRunResponse,
)
from app.services.approval_service import ApprovalService
from app.services.stale_runs import StaleRunService
from app.services.task_runner import RedisTaskRunner

router = APIRouter(prefix="/ops", tags=["ops"])


def _count_by_status(db: Session, model) -> dict[str, int]:
    rows = db.query(model.status).all()
    counts: dict[str, int] = {}
    for (status,) in rows:
        key = str(status or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


@router.get("/readiness")
def readiness(db: Session = Depends(get_db)) -> dict:
    checks = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"failed:{exc.__class__.__name__}"
    settings = get_settings()
    checks["llm_configured"] = "ok" if settings.effective_llm_api_key else "missing"
    checks["redis_enabled"] = settings.redis_enabled
    if settings.redis_enabled:
        try:
            get_redis_client().ping()
            checks["redis"] = "ok"
        except RedisUnavailableError as exc:
            checks["redis"] = f"failed:{exc}"
    else:
        checks["redis"] = "disabled"
    checks["embedding_provider"] = settings.embedding_provider
    checks["reranker_provider"] = settings.reranker_provider if settings.reranker_enabled else "disabled"
    checks["stale_running_count"] = len(StaleRunService().find_stale(db))
    status = "ready" if checks["database"] == "ok" else "degraded"
    return {"status": status, "checks": checks}


@router.get("/metrics")
def metrics(db: Session = Depends(get_db)) -> dict:
    latest_eval = db.query(EvaluationRun).order_by(EvaluationRun.created_at.desc()).first()
    return {
        "app": telemetry.snapshot(),
        "database": {
            "url_scheme": engine.url.get_backend_name(),
            "agent_runs_by_status": _count_by_status(db, AgentRun),
            "tasks_by_status": _count_by_status(db, TaskRun),
            "llm_calls_by_status": _count_by_status(db, LLMCallLog),
            "evaluation_run_count": db.query(EvaluationRun).count(),
        },
        "latest_evaluation": {
            "id": latest_eval.id,
            "name": latest_eval.name,
            "summary": latest_eval.summary_json,
        }
        if latest_eval
        else None,
    }


@router.get("/config")
def config_summary(_: None = Depends(require_admin)) -> dict:
    settings = get_settings()
    return {
        "app_env": settings.app_env,
        "database_backend": engine.url.get_backend_name(),
        "llm": {
            "configured": bool(settings.effective_llm_api_key),
            "base_url": settings.effective_llm_base_url,
            "model": settings.llm_model,
            "thinking_mode": settings.llm_thinking_mode,
            "fallback_enabled": settings.llm_fallback_enabled,
        },
        "retrieval": {
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model_name,
            "embedding_fallback": settings.embedding_provider_fallback,
            "vector_backend": settings.vector_backend,
            "reranker_enabled": settings.reranker_enabled,
            "reranker_provider": settings.reranker_provider,
            "reranker_fallback": settings.reranker_provider_fallback,
        },
        "security": {
            "admin_token_configured": bool(settings.admin_api_key),
            "require_admin_for_mutations": settings.require_admin_for_mutations,
        },
        "queue": {
            "redis_enabled": settings.redis_enabled,
            "redis_url": settings.redis_url,
            "queue_name": settings.redis_queue_name,
            "run_lock_ttl_seconds": settings.redis_run_lock_ttl_seconds,
            "heartbeat_ttl_seconds": settings.redis_heartbeat_ttl_seconds,
            "active_run_limit_per_profile": settings.agent_active_run_limit_per_profile,
        },
    }


@router.get("/queue/status")
def queue_status(_: None = Depends(require_admin)) -> dict:
    settings = get_settings()
    if not settings.redis_enabled:
        raise HTTPException(status_code=503, detail="Redis is disabled.")
    try:
        return RedisTaskRunner().queue_status()
    except RedisUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/queue/recover-queued")
def recover_queued_runs(
    older_than_minutes: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> dict:
    settings = get_settings()
    if not settings.redis_enabled:
        raise HTTPException(status_code=503, detail="Redis is disabled.")
    try:
        recovered = RedisTaskRunner().recover_queued_agent_runs(db, older_than_minutes=older_than_minutes)
    except RedisUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"recovered_count": len(recovered), "recovered_runs": recovered}


@router.get("/agent-runs/stale")
def stale_agent_runs(
    threshold_minutes: int | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> dict:
    return {"stale_runs": StaleRunService().find_stale(db, threshold_minutes=threshold_minutes)}


@router.post("/agent-runs/mark-stale", response_model=list[AgentRunResponse])
def mark_stale_agent_runs(
    threshold_minutes: int | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> list[AgentRunResponse]:
    runs = StaleRunService().mark_stale(db, threshold_minutes=threshold_minutes)
    return [AgentRunResponse.model_validate(run) for run in runs]


@router.get("/approvals", response_model=list[AgentApprovalResponse])
def list_approvals(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> list[AgentApprovalResponse]:
    query = db.query(AgentApproval)
    if status_filter:
        query = query.filter(AgentApproval.status == status_filter)
    rows = query.order_by(AgentApproval.created_at.desc()).limit(limit).all()
    return [AgentApprovalResponse.model_validate(row) for row in rows]


@router.post("/approvals", response_model=AgentApprovalResponse, status_code=status.HTTP_201_CREATED)
def create_approval(
    payload: AgentApprovalCreateRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> AgentApprovalResponse:
    if db.query(AgentRun).filter(AgentRun.id == payload.run_id).first() is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    try:
        approval = ApprovalService().get_or_create_pending(
            db,
            run_id=payload.run_id,
            action_type=payload.action_type,
            payload_summary=payload.payload_summary_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AgentApprovalResponse.model_validate(approval)


@router.post("/approvals/{approval_id}/decision", response_model=AgentApprovalResponse)
def decide_approval(
    approval_id: int,
    payload: AgentApprovalDecisionRequest,
    db: Session = Depends(get_db),
    _: None = Depends(require_admin),
) -> AgentApprovalResponse:
    approval = db.query(AgentApproval).filter(AgentApproval.id == approval_id).first()
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found.")
    try:
        approval = ApprovalService().decide(
            db,
            approval=approval,
            approved=payload.approved,
            note=payload.note,
            decided_by_user_id=payload.decided_by_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentApprovalResponse.model_validate(approval)
