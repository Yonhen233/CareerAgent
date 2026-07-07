from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import OpsAuditEvent


class OpsAuditService:
    def record(
        self,
        db: Session,
        *,
        event_type: str,
        target_type: str,
        payload: dict[str, Any],
        actor: str | None = None,
        target_id: str | int | None = None,
    ) -> OpsAuditEvent:
        row = OpsAuditEvent(
            event_type=event_type,
            actor=actor,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            payload_json=payload,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
