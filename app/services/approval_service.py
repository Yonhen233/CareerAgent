from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import AgentApproval


APPROVAL_ACTION_TYPES = {"application_packet", "browser_apply", "email_draft", "email_send"}


class ApprovalService:
    def payload_hash(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_or_create_pending(
        self,
        db: Session,
        *,
        run_id: int,
        action_type: str,
        payload_summary: dict[str, Any],
    ) -> AgentApproval:
        if action_type not in APPROVAL_ACTION_TYPES:
            raise ValueError(f"Unsupported approval action_type: {action_type}.")
        digest = self.payload_hash(payload_summary)
        existing = (
            db.query(AgentApproval)
            .filter(
                AgentApproval.run_id == run_id,
                AgentApproval.action_type == action_type,
                AgentApproval.payload_hash == digest,
            )
            .order_by(AgentApproval.id.desc())
            .first()
        )
        if existing is not None:
            return existing
        row = AgentApproval(
            run_id=run_id,
            action_type=action_type,
            status="pending",
            payload_hash=digest,
            payload_summary_json=payload_summary,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def decide(
        self,
        db: Session,
        *,
        approval: AgentApproval,
        approved: bool,
        note: str | None = None,
        decided_by_user_id: str | None = None,
    ) -> AgentApproval:
        if approval.status not in {"pending", "approved", "rejected"}:
            raise ValueError(f"Approval {approval.id} cannot be decided from status {approval.status}.")
        target = "approved" if approved else "rejected"
        if approval.status == target:
            return approval
        approval.status = target
        approval.note = note
        approval.decided_by_user_id = decided_by_user_id
        approval.decided_at = datetime.now(timezone.utc)
        db.add(approval)
        db.commit()
        db.refresh(approval)
        return approval

    def cancel_pending_for_run(self, db: Session, *, run_id: int, note: str | None = None) -> int:
        rows = (
            db.query(AgentApproval)
            .filter(AgentApproval.run_id == run_id, AgentApproval.status == "pending")
            .all()
        )
        now = datetime.now(timezone.utc)
        for row in rows:
            row.status = "cancelled"
            row.note = note
            row.decided_at = now
            db.add(row)
        db.commit()
        return len(rows)

    def cancel_unexecuted_for_run(
        self,
        db: Session,
        *,
        run_id: int,
        executed_approval_ids: set[int] | None = None,
        note: str | None = None,
    ) -> int:
        executed = executed_approval_ids or set()
        rows = (
            db.query(AgentApproval)
            .filter(
                AgentApproval.run_id == run_id,
                AgentApproval.status.in_(["pending", "approved"]),
            )
            .all()
        )
        now = datetime.now(timezone.utc)
        cancelled = 0
        for row in rows:
            if row.id in executed:
                continue
            row.status = "cancelled"
            row.note = note
            row.decided_at = now
            db.add(row)
            cancelled += 1
        db.commit()
        return cancelled
