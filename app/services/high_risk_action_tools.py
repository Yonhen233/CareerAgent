from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import AgentApproval, AgentRun
from app.services.approval_service import ApprovalService
from app.services.agent_runtime import AgentToolRuntime
from app.services.ops_audit import OpsAuditService
from app.services.outbound_tools import BrowserApplyTool, EmailOutboundTool
from app.services.trace_service import TraceService
from app.agents.tools import bind_agent_tool


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
        tool_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        approval = db.query(AgentApproval).filter(AgentApproval.id == approval_id).first()
        if approval is None:
            raise ValueError(f"Approval {approval_id} was not found.")
        if approval.action_type not in HIGH_RISK_TOOL_ACTIONS:
            raise ValueError(f"Approval {approval_id} is not bound to a high-risk tool action.")
        run = db.query(AgentRun).filter(AgentRun.id == approval.run_id).first()
        if run is None or run.status in {"cancelled", "withdrawn"}:
            raise ApprovalRequiredError(
                f"{approval.action_type} cannot execute because Agent run {approval.run_id} is cancelled or withdrawn."
            )
        if approval.status != "approved":
            raise ApprovalRequiredError(
                f"{approval.action_type} requires an approved approval record before tool execution."
            )
        claimed = (
            db.query(AgentApproval)
            .filter(AgentApproval.id == approval.id, AgentApproval.status == "approved")
            .update({AgentApproval.status: "executing"}, synchronize_session=False)
        )
        db.commit()
        if claimed != 1:
            raise ApprovalRequiredError(
                f"{approval.action_type} approval {approval.id} was already claimed by another executor."
            )
        db.refresh(approval)
        execution_payload = {
            **(approval.payload_summary_json or {}),
            **(tool_payload or {}),
            "approval_id": approval.id,
        }
        try:
            trace = TraceService()
            tool_result = AgentToolRuntime().execute_sync(
                db,
                run_id=approval.run_id,
                step_name=f"approved_{approval.action_type}",
                input_json=execution_payload,
                tool=bind_agent_tool(
                    approval.action_type,
                    lambda: self._execute_tool(
                        action_type=approval.action_type,
                        payload=execution_payload,
                        run_id=approval.run_id,
                    ),
                ),
                event_sink=lambda event_type, payload: trace.add_event(
                    db,
                    run_id=approval.run_id,
                    event_type=event_type,
                    node_name=f"approved_{approval.action_type}",
                    payload=payload,
                ),
            )
        except Exception as exc:
            approval.status = "execution_failed"
            approval.note = f"{exc.__class__.__name__}: {exc}"[:2000]
            db.add(approval)
            db.commit()
            failure_payload = {
                "status": "tool_execution_failed",
                "action_type": approval.action_type,
                "approval_id": approval.id,
                "run_id": approval.run_id,
                "error": str(exc),
                "failed_at": datetime.now(timezone.utc).isoformat(),
            }
            TraceService().add_artifact(
                db,
                run_id=approval.run_id,
                artifact_type=f"{approval.action_type}_result",
                payload=failure_payload,
            )
            OpsAuditService().record(
                db,
                event_type=f"{approval.action_type}_tool_execution_failed",
                target_type="agent_approval",
                target_id=approval.id,
                actor=actor,
                payload=failure_payload,
            )
            raise

        approval.status = "executed"
        db.add(approval)
        db.commit()
        db.refresh(approval)
        result = {
            "status": "tool_execution_completed",
            "action_type": approval.action_type,
            "approval_id": approval.id,
            "run_id": approval.run_id,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "payload_summary_json": approval.payload_summary_json,
            "tool_result": tool_result,
        }
        TraceService().add_artifact(
            db,
            run_id=approval.run_id,
            artifact_type=f"{approval.action_type}_result",
            payload=result,
        )
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

    def _execute_tool(self, *, action_type: str, payload: dict[str, Any], run_id: int) -> dict[str, Any]:
        if action_type == "email_draft":
            return EmailOutboundTool().create_draft(payload, run_id=run_id)
        if action_type == "email_send":
            return EmailOutboundTool().send_email(payload, run_id=run_id)
        if action_type == "browser_apply":
            return BrowserApplyTool().apply(payload, run_id=run_id)
        raise ValueError(f"Unsupported high-risk action_type: {action_type}.")
