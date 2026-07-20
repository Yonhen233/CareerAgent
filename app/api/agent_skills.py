from typing import Any

from fastapi import APIRouter, HTTPException

from app.agents.skills import get_agent_skill, list_agent_skills
from app.agents.subagents import list_subagents


router = APIRouter(prefix="/agent", tags=["agent-capabilities"])


@router.get("/skills")
def get_agent_skills() -> list[dict[str, Any]]:
    return list_agent_skills(include_deferred=True)


@router.get("/skills/{skill_name}")
def get_agent_skill_detail(skill_name: str) -> dict[str, Any]:
    try:
        return get_agent_skill(skill_name, include_instructions=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/subagents")
def get_agent_subagents() -> list[dict[str, Any]]:
    return list_subagents()
