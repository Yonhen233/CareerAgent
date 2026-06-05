from typing import Any

from fastapi import APIRouter

from app.agents.tools import list_agent_tools


router = APIRouter(prefix="/agent/tools", tags=["agent-tools"])


@router.get("")
def get_agent_tools() -> list[dict[str, Any]]:
    return list_agent_tools()
