from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agents.orchestrator import AgentOrchestrator
from app.core.database import get_db
from app.models.entities import AgentRun, AgentStep
from app.models.schemas import AgentRunRequest, AgentRunResponse, AgentStepResponse

router = APIRouter(prefix="/agent/runs", tags=["agent-runs"])


@router.post("", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_run(payload: AgentRunRequest, db: Session = Depends(get_db)) -> AgentRunResponse:
    run = await AgentOrchestrator().run(db, payload)
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


@router.get("/{run_id}/steps", response_model=list[AgentStepResponse])
def get_agent_steps(run_id: int, db: Session = Depends(get_db)) -> list[AgentStepResponse]:
    rows = db.query(AgentStep).filter(AgentStep.run_id == run_id).order_by(AgentStep.id.asc()).all()
    return [AgentStepResponse.model_validate(row) for row in rows]
