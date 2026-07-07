from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import AgentApproval, AgentRun
from app.services.approval_service import ApprovalService
from app.services.ops_audit import OpsAuditService
from app.services.trace_service import TraceService


HIGH_RISK_TOOL_ACTIONS = {"browser_apply", "email_draft", "email_send"}


class ApprovalRequiredError(RuntimeError):
    pass


class HighRiskActionToolService:
    def request_approval(
        self,
        db: Session,
        *,
        run_id: int,
        action_type: str,
        payload_summary: dict[str, Any],
    ) -> AgentApproval:
        if action_type not in HIGH_RISK_TOOL_ACTIONS:
            raise ValueError(f"Unsupported high-risk action_type: {action_type}.")
        if db.query(AgentRun).filter(AgentRun.id == run_id).first() is None:
            raise ValueError(f"Agent run {run_id} was not found.")
        approval = ApprovalService().get_or_create_pending(
            db,
            run_id=run_id,
            action_type=action_type,
            payload_summary=payload_summary,
        )
        TraceService().add_event(
            db,
            run_id=run_id,
            event_type="high_risk_action_approval_requested",
            payload={"approval_id": approval.id, "action_type": action_type, "payload_summary": payload_summary},
        )
        return approval

    def execute_after_approval(
        self,
        db: Session,
        *,
        approval_id: int,
        actor: str | None = None,
    ) -> dict[str, Any]:
        approval = db.query(AgentApproval).filter(AgentApproval.id == approval_id).first()
        if approval is None:
            raise ValueError(f"Approval {approval_id} was not found.")
        if approval.action_type not in HIGH_RISK_TOOL_ACTIONS:
            raise ValueError(f"Approval {approval_id} is not bound to a high-risk tool action.")
        if approval.status != "approved":
            raise ApprovalRequiredError(
                f"{approval.action_type} requires an approved approval record before tool execution."
            )
        result = {
            "status": "ready_for_tool_execution",
            "action_type": approval.action_type,
            "approval_id": approval.id,
            "run_id": approval.run_id,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "payload_summary_json": approval.payload_summary_json,
        }
        OpsAuditService().record(
            db,
            event_type=f"{approval.action_type}_tool_execution_released",
            target_type="agent_approval",
            target_id=approval.id,
            actor=actor,
            payload=result,
        )
        TraceService().add_event(
            db,
            run_id=approval.run_id,
            event_type=f"{approval.action_type}_tool_execution_released",
            payload=result,
        )
        return result
