from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import (
    AgentArtifact,
    AgentRun,
    AgentRunControlAction,
    Application,
    InterviewPrep,
    ResumeVersion,
)
from app.services.approval_service import ApprovalService
from app.services.ops_audit import OpsAuditService
from app.services.trace_service import TraceService


ACTIVE_RUN_STATUSES = {"queued", "running", "waiting_for_confirmation"}


class RunWithdrawalConflict(RuntimeError):
    def __init__(self, message: str, *, irreversible_actions: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.irreversible_actions = irreversible_actions or []


class RunControlService:
    """Audit run recovery/rewind operations and compensate reversible business artifacts."""

    def create_action(
        self,
        db: Session,
        *,
        run_id: int,
        action_type: str,
        actor: str | None = None,
        source_checkpoint_id: str | None = None,
        target_run_id: int | None = None,
        payload: dict[str, Any] | None = None,
        status: str = "requested",
    ) -> AgentRunControlAction:
        row = AgentRunControlAction(
            run_id=run_id,
            action_type=action_type,
            status=status,
            actor=actor,
            source_checkpoint_id=source_checkpoint_id,
            target_run_id=target_run_id,
            payload_json=payload or {},
            completed_at=datetime.now(timezone.utc) if status in {"completed", "blocked", "failed"} else None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def complete_action(
        self,
        db: Session,
        action: AgentRunControlAction,
        *,
        status: str,
        payload: dict[str, Any] | None = None,
        target_run_id: int | None = None,
    ) -> AgentRunControlAction:
        action.status = status
        action.completed_at = datetime.now(timezone.utc)
        action.target_run_id = target_run_id if target_run_id is not None else action.target_run_id
        action.payload_json = {**(action.payload_json or {}), **(payload or {})}
        db.add(action)
        db.commit()
        db.refresh(action)
        return action

    def withdrawal_preview(self, db: Session, run: AgentRun) -> dict[str, Any]:
        prefix = self._idempotency_prefix(run.id)
        resume_versions = db.query(ResumeVersion).filter(ResumeVersion.idempotency_key.like(f"{prefix}%")).all()
        applications = db.query(Application).filter(Application.idempotency_key.like(f"{prefix}%")).all()
        interview_preps = db.query(InterviewPrep).filter(InterviewPrep.idempotency_key.like(f"{prefix}%")).all()
        irreversible = self._irreversible_actions(db, run.id)
        return {
            "run_id": run.id,
            "run_status": run.status,
            "can_withdraw_now": run.status not in ACTIVE_RUN_STATUSES and not irreversible,
            "requires_cancel_first": run.status in ACTIVE_RUN_STATUSES,
            "irreversible_actions": irreversible,
            "owned_artifacts": {
                "resume_version_ids": [row.id for row in resume_versions],
                "application_ids": [row.id for row in applications],
                "interview_prep_ids": [row.id for row in interview_preps],
            },
            "policy": (
                "撤回只停用本次 run 生成的内部材料并保留审计；共享简历档案、岗位和匹配语料不删除。"
                "已发送邮件或已提交网页表单不可逆。"
            ),
        }

    def withdraw(
        self,
        db: Session,
        *,
        run: AgentRun,
        reason: str,
        actor: str | None = None,
    ) -> tuple[AgentRun, AgentRunControlAction]:
        already_withdrawn = run.status == "withdrawn"
        if run.status in ACTIVE_RUN_STATUSES:
            raise RunWithdrawalConflict(
                "运行中的流程必须先取消并等待 worker 停止，之后才能撤回已生成材料。"
            )

        preview = self.withdrawal_preview(db, run)
        action = self.create_action(
            db,
            run_id=run.id,
            action_type="withdraw",
            actor=actor,
            payload={"reason": reason, "preview": preview, "idempotent_reconciliation": already_withdrawn},
        )
        if preview["irreversible_actions"]:
            self.complete_action(
                db,
                action,
                status="blocked",
                payload={"blocked_reason": "irreversible_external_side_effect"},
            )
            raise RunWithdrawalConflict(
                "该流程已经执行不可逆的外部操作，不能标记为已撤回。可以停止后续动作，但不能伪装成邮件未发送或表单未提交。",
                irreversible_actions=preview["irreversible_actions"],
            )

        now = datetime.now(timezone.utc)
        prefix = self._idempotency_prefix(run.id)
        resume_versions = db.query(ResumeVersion).filter(ResumeVersion.idempotency_key.like(f"{prefix}%")).all()
        applications = db.query(Application).filter(Application.idempotency_key.like(f"{prefix}%")).all()
        interview_preps = db.query(InterviewPrep).filter(InterviewPrep.idempotency_key.like(f"{prefix}%")).all()

        for row in resume_versions:
            row.lifecycle_status = "withdrawn"
            row.withdrawn_at = now
            row.withdrawal_reason = reason
            db.add(row)
        for row in applications:
            row.status = "withdrawn"
            row.withdrawn_at = now
            row.withdrawal_reason = reason
            row.automation_result_json = {
                **(row.automation_result_json or {}),
                "withdrawal": {"withdrawn_at": now.isoformat(), "reason": reason, "actor": actor},
            }
            db.add(row)
        for row in interview_preps:
            row.lifecycle_status = "withdrawn"
            row.withdrawn_at = now
            row.withdrawal_reason = reason
            db.add(row)

        executed_approval_ids = self._executed_approval_ids(db, run.id)
        ApprovalService().cancel_unexecuted_for_run(
            db,
            run_id=run.id,
            executed_approval_ids=executed_approval_ids,
            note=f"run withdrawn: {reason}",
        )
        output = dict(run.output_json or {})
        withdrawal = {
            "withdrawn_at": now.isoformat(),
            "reason": reason,
            "actor": actor,
            "compensated": preview["owned_artifacts"],
            "audit_preserved": True,
        }
        output["withdrawal"] = withdrawal
        run.status = "withdrawn"
        run.output_json = output
        db.add(run)
        db.commit()
        db.refresh(run)

        TraceService().add_artifact(db, run_id=run.id, artifact_type="run_withdrawal", payload=withdrawal)
        TraceService().add_event(db, run_id=run.id, event_type="run_withdrawn", payload=withdrawal)
        OpsAuditService().record(
            db,
            event_type="agent_run_withdrawn",
            target_type="agent_run",
            target_id=run.id,
            actor=actor,
            payload=withdrawal,
        )
        self.complete_action(db, action, status="completed", payload={"withdrawal": withdrawal})
        return run, action

    def _irreversible_actions(self, db: Session, run_id: int) -> list[dict[str, Any]]:
        rows = (
            db.query(AgentArtifact)
            .filter(
                AgentArtifact.run_id == run_id,
                AgentArtifact.artifact_type.in_(["email_send_result", "browser_apply_result"]),
            )
            .order_by(AgentArtifact.id.asc())
            .all()
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            payload = row.artifact_json or {}
            tool_result = payload.get("tool_result") or {}
            irreversible = row.artifact_type == "email_send_result" and tool_result.get("status") == "email_sent"
            irreversible = irreversible or (
                row.artifact_type == "browser_apply_result" and bool(tool_result.get("submitted"))
            )
            if irreversible:
                output.append(
                    {
                        "artifact_id": row.id,
                        "artifact_type": row.artifact_type,
                        "status": tool_result.get("status"),
                        "executed_at": payload.get("executed_at") or tool_result.get("completed_at"),
                    }
                )
        return output

    def _executed_approval_ids(self, db: Session, run_id: int) -> set[int]:
        rows = (
            db.query(AgentArtifact)
            .filter(AgentArtifact.run_id == run_id, AgentArtifact.artifact_type.like("%_result"))
            .all()
        )
        return {
            int(row.artifact_json["approval_id"])
            for row in rows
            if (row.artifact_json or {}).get("approval_id")
            and (row.artifact_json or {}).get("status") == "tool_execution_completed"
        }

    @staticmethod
    def _idempotency_prefix(run_id: int) -> str:
        return f"agent_run:{run_id}:"
