from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import AuthContext, has_admin_access, optional_auth_context, require_admin
from app.models.entities import (
    AgentFeedback,
    AgentMemory,
    AgentQualityReview,
    AgentRun,
    Profile,
    ToolCircuitState,
)
from app.models.schemas import (
    AgentFeedbackCreateRequest,
    AgentFeedbackResponse,
    AgentMemoryCreateRequest,
    AgentMemoryResponse,
    AgentQualityReviewResolveRequest,
    AgentQualityReviewResponse,
)
from app.services.memory_feedback import AgentFeedbackService, CareerMemoryService


router = APIRouter(prefix="/agent", tags=["agent-governance"])
ops_router = APIRouter(prefix="/ops", tags=["ops-agent-governance"])


def _tenant_run(db: Session, run_id: int, auth: AuthContext) -> AgentRun:
    query = db.query(AgentRun).filter(AgentRun.id == run_id)
    if get_settings().rbac_enabled:
        query = query.filter(AgentRun.tenant_id == auth.tenant_id)
        if not has_admin_access(auth):
            query = query.filter(or_(AgentRun.user_id == auth.user_id, AgentRun.user_id.is_(None)))
    run = query.first()
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found.")
    return run


def _validate_profile(db: Session, profile_id: int | None, auth: AuthContext) -> None:
    if profile_id is None:
        return
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    if get_settings().rbac_enabled and profile.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="Profile not found.")


@router.get("/memories", response_model=list[AgentMemoryResponse])
def list_agent_memories(
    profile_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> list[AgentMemoryResponse]:
    _validate_profile(db, profile_id, auth)
    rows = CareerMemoryService().list_active(
        db,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        profile_id=profile_id,
        limit=limit,
    )
    return [AgentMemoryResponse.model_validate(row) for row in rows]


@router.post("/memories", response_model=AgentMemoryResponse, status_code=status.HTTP_201_CREATED)
def create_agent_memory(
    payload: AgentMemoryCreateRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> AgentMemoryResponse:
    _validate_profile(db, payload.profile_id, auth)
    row = CareerMemoryService().upsert(
        db,
        memory_type=payload.memory_type,
        memory_key=payload.memory_key,
        value_json=payload.value_json,
        confidence=payload.confidence,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        profile_id=payload.profile_id,
        source_type="explicit_user",
    )
    return AgentMemoryResponse.model_validate(row)


@router.delete("/memories/{memory_id}", response_model=AgentMemoryResponse)
def deactivate_agent_memory(
    memory_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> AgentMemoryResponse:
    query = db.query(AgentMemory).filter(AgentMemory.id == memory_id)
    if get_settings().rbac_enabled:
        query = query.filter(AgentMemory.tenant_id == auth.tenant_id)
    row = query.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Agent memory not found.")
    return AgentMemoryResponse.model_validate(CareerMemoryService().deactivate(db, row))


@router.post("/runs/{run_id}/feedback", response_model=AgentFeedbackResponse, status_code=status.HTTP_201_CREATED)
def create_agent_feedback(
    run_id: int,
    payload: AgentFeedbackCreateRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> AgentFeedbackResponse:
    run = _tenant_run(db, run_id, auth)
    row = AgentFeedbackService().record(
        db,
        run=run,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        verdict=payload.verdict,
        rating=payload.rating,
        reason_tags=payload.reason_tags,
        comment=payload.comment,
        correction_json=payload.correction_json,
    )
    return AgentFeedbackResponse.model_validate(row)


@router.get("/runs/{run_id}/feedback", response_model=list[AgentFeedbackResponse])
def list_agent_feedback(
    run_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(optional_auth_context),
) -> list[AgentFeedbackResponse]:
    _tenant_run(db, run_id, auth)
    rows = (
        db.query(AgentFeedback)
        .filter(AgentFeedback.run_id == run_id)
        .order_by(AgentFeedback.id.desc())
        .all()
    )
    return [AgentFeedbackResponse.model_validate(row) for row in rows]


@ops_router.get("/agent-quality/reviews", response_model=list[AgentQualityReviewResponse])
def list_quality_reviews(
    review_status: str = Query(default="open", alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_admin),
) -> list[AgentQualityReviewResponse]:
    query = db.query(AgentQualityReview)
    if review_status != "all":
        query = query.filter(AgentQualityReview.status == review_status)
    if get_settings().rbac_enabled:
        query = query.filter(AgentQualityReview.tenant_id == auth.tenant_id)
    rows = query.order_by(AgentQualityReview.id.desc()).limit(limit).all()
    return [AgentQualityReviewResponse.model_validate(row) for row in rows]


@ops_router.post("/agent-quality/reviews/{review_id}/resolve", response_model=AgentQualityReviewResponse)
def resolve_quality_review(
    review_id: int,
    payload: AgentQualityReviewResolveRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_admin),
) -> AgentQualityReviewResponse:
    query = db.query(AgentQualityReview).filter(AgentQualityReview.id == review_id)
    if get_settings().rbac_enabled:
        query = query.filter(AgentQualityReview.tenant_id == auth.tenant_id)
    row = query.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Quality review not found.")
    if row.status != "open":
        raise HTTPException(status_code=409, detail="Quality review is already resolved.")
    row.status = "resolved"
    row.resolution_note = payload.resolution_note
    row.resolved_by = auth.actor
    row.resolved_at = datetime.now(timezone.utc)
    db.add(row)
    db.commit()
    db.refresh(row)
    return AgentQualityReviewResponse.model_validate(row)


@ops_router.get("/agent-runtime/circuits")
def list_tool_circuits(
    db: Session = Depends(get_db),
    _auth: AuthContext = Depends(require_admin),
) -> list[dict]:
    rows = db.query(ToolCircuitState).order_by(ToolCircuitState.updated_at.desc()).all()
    return [
        {
            "id": row.id,
            "tool_name": row.tool_name,
            "scope_key": row.scope_key,
            "status": row.status,
            "consecutive_failures": row.consecutive_failures,
            "last_error_category": row.last_error_category,
            "open_until": row.open_until,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@ops_router.post("/agent-runtime/circuits/{circuit_id}/reset")
def reset_tool_circuit(
    circuit_id: int,
    db: Session = Depends(get_db),
    _auth: AuthContext = Depends(require_admin),
) -> dict:
    row = db.query(ToolCircuitState).filter(ToolCircuitState.id == circuit_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Tool circuit not found.")
    row.status = "closed"
    row.consecutive_failures = 0
    row.last_error_category = None
    row.opened_at = None
    row.open_until = None
    db.add(row)
    db.commit()
    return {"id": row.id, "status": row.status}
