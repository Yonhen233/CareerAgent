from typing import Any

from fastapi import APIRouter

from app.agents.tools import list_agent_tools
from app.services.agent_harness import AgentHarnessService


router = APIRouter(prefix="/agent/tools", tags=["agent-tools"])


@router.get("")
def get_agent_tools() -> list[dict[str, Any]]:
    return list_agent_tools()


@router.get("/harness")
def get_agent_harness_manifest() -> dict[str, Any]:
    return AgentHarnessService().manifest()
