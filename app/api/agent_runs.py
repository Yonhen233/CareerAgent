from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agents.orchestrator import AgentOrchestrator
from app.core.database import get_db
from app.models.entities import AgentRun, AgentStep
from app.models.schemas import AgentRunRequest, AgentRunResponse, AgentRunResumeRequest, AgentStepResponse

router = APIRouter(prefix="/agent/runs", tags=["agent-runs"])


@router.post("", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_run(payload: AgentRunRequest, db: Session = Depends(get_db)) -> AgentRunResponse:
    run = await AgentOrchestrator().run(db, payload)
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
