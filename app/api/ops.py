from sqlalchemy import text
from sqlalchemy.orm import Session

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.core.database import engine, get_db
from app.core.security import require_admin
from app.core.telemetry import telemetry
from app.models.entities import AgentRun, EvaluationRun, LLMCallLog, TaskRun

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
    checks["embedding_provider"] = settings.embedding_provider
    checks["reranker_provider"] = settings.reranker_provider if settings.reranker_enabled else "disabled"
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
    }
