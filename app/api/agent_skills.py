from typing import Any

from fastapi import APIRouter

from app.agents.skills import list_agent_skills
from app.agents.subagents import list_subagents


router = APIRouter(prefix="/agent", tags=["agent-capabilities"])


@router.get("/skills")
def get_agent_skills() -> list[dict[str, Any]]:
    return list_agent_skills(include_deferred=True)


@router.get("/subagents")
def get_agent_subagents() -> list[dict[str, Any]]:
    return list_subagents()
