from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.entities import (
    AgentArtifact,
    AgentFeedback,
    AgentMemory,
    AgentQualityReview,
    AgentRun,
)


class CareerMemoryService:
    """Durable typed memory; raw chat history is deliberately not persisted as memory."""

    ALLOWED_TYPES = {"preference", "constraint", "decision", "outcome", "correction"}

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def upsert(
        self,
        db: Session,
        *,
        memory_type: str,
        memory_key: str,
        value_json: dict[str, Any],
        tenant_id: str | None,
        user_id: str | None = None,
        profile_id: int | None = None,
        confidence: float = 1.0,
        source_type: str = "explicit_user",
        source_run_id: int | None = None,
    ) -> AgentMemory:
        if memory_type not in self.ALLOWED_TYPES:
            raise ValueError(f"Unsupported memory_type: {memory_type}.")
        if not memory_key.strip():
            raise ValueError("memory_key is required.")
        query = db.query(AgentMemory).filter(
            AgentMemory.memory_type == memory_type,
            AgentMemory.memory_key == memory_key.strip(),
            AgentMemory.status == "active",
        )
        query = query.filter(AgentMemory.tenant_id == tenant_id)
        if profile_id is None:
            query = query.filter(AgentMemory.profile_id.is_(None))
        else:
            query = query.filter(AgentMemory.profile_id == profile_id)
        if user_id is None:
            query = query.filter(AgentMemory.user_id.is_(None))
        else:
            query = query.filter(AgentMemory.user_id == user_id)
        for previous in query.all():
            previous.status = "superseded"
            db.add(previous)
        row = AgentMemory(
            tenant_id=tenant_id,
            user_id=user_id,
            profile_id=profile_id,
            memory_type=memory_type,
            memory_key=memory_key.strip(),
            value_json=value_json,
            confidence=max(0.0, min(float(confidence), 1.0)),
            source_type=source_type,
            source_run_id=source_run_id,
            status="active",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def list_active(
        self,
        db: Session,
        *,
        tenant_id: str | None,
        user_id: str | None = None,
        profile_id: int | None = None,
        limit: int | None = None,
    ) -> list[AgentMemory]:
        now = datetime.now(timezone.utc)
        query = db.query(AgentMemory).filter(
            AgentMemory.tenant_id == tenant_id,
            AgentMemory.status == "active",
        )
        if profile_id is not None:
            query = query.filter(AgentMemory.profile_id == profile_id)
        if user_id is None:
            query = query.filter(AgentMemory.user_id.is_(None))
        else:
            query = query.filter(or_(AgentMemory.user_id == user_id, AgentMemory.user_id.is_(None)))
        rows = query.order_by(AgentMemory.updated_at.desc(), AgentMemory.id.desc()).all()
        active = [row for row in rows if row.expires_at is None or self._aware(row.expires_at) > now]
        return active[: (limit or self.settings.agent_memory_context_max_items)]

    def compact_context(
        self,
        db: Session,
        *,
        tenant_id: str | None,
        user_id: str | None = None,
        profile_id: int | None = None,
    ) -> dict[str, Any]:
        rows = self.list_active(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            profile_id=profile_id,
        )
        items: list[dict[str, Any]] = []
        used = 0
        for row in rows:
            item = {
                "type": row.memory_type,
                "key": row.memory_key,
                "value": row.value_json,
                "confidence": row.confidence,
                "source": row.source_type,
            }
            encoded = json.dumps(item, ensure_ascii=False, default=str)
            if items and used + len(encoded) > self.settings.agent_memory_context_max_chars:
                break
            items.append(item)
            used += len(encoded)
        return {
            "version": "careeragent-typed-memory-v1",
            "items": items,
            "item_count": len(items),
            "context_chars": used,
            "policy": "typed_facts_only_no_raw_chat_replay",
        }

    def learn_run_episodes(self, db: Session, *, run: AgentRun) -> list[AgentMemory]:
        if run.status != "completed":
            return []
        artifacts = (
            db.query(AgentArtifact)
            .filter(AgentArtifact.run_id == run.id)
            .order_by(AgentArtifact.id.asc())
            .all()
        )
        learned: list[AgentMemory] = []
        selected = next((row for row in reversed(artifacts) if row.artifact_type == "selected_job"), None)
        if selected:
            job = dict((selected.artifact_json or {}).get("selected_job") or {})
            job_id = job.get("job_id")
            if job_id:
                learned.append(
                    self.upsert(
                        db,
                        memory_type="decision",
                        memory_key=f"selected_job:{job_id}",
                        value_json=job,
                        tenant_id=run.tenant_id,
                        user_id=run.user_id,
                        profile_id=run.profile_id,
                        source_type="verified_run_artifact",
                        source_run_id=run.id,
                    )
                )
        application = next(
            (row for row in reversed(artifacts) if row.artifact_type == "application_packet"),
            None,
        )
        if application:
            payload = dict(application.artifact_json or {})
            application_id = payload.get("application_id")
            if application_id:
                learned.append(
                    self.upsert(
                        db,
                        memory_type="outcome",
                        memory_key=f"application_prepared:{application_id}",
                        value_json={
                            "application_id": application_id,
                            "job_id": payload.get("job_id"),
                            "status": "prepared_not_sent",
                        },
                        tenant_id=run.tenant_id,
                        user_id=run.user_id,
                        profile_id=run.profile_id,
                        source_type="verified_run_artifact",
                        source_run_id=run.id,
                    )
                )
        return learned

    @staticmethod
    def deactivate(db: Session, memory: AgentMemory) -> AgentMemory:
        memory.status = "inactive"
        db.add(memory)
        db.commit()
        db.refresh(memory)
        return memory

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class AgentFeedbackService:
    NEGATIVE_VERDICTS = {"incorrect", "incomplete", "unsafe"}

    def record(
        self,
        db: Session,
        *,
        run: AgentRun,
        tenant_id: str | None,
        user_id: str | None,
        verdict: str,
        rating: int | None,
        reason_tags: list[str],
        comment: str | None,
        correction_json: dict[str, Any],
    ) -> AgentFeedback:
        if verdict not in {"helpful", "incorrect", "incomplete", "unsafe"}:
            raise ValueError(f"Unsupported feedback verdict: {verdict}.")
        if rating is not None and rating not in {1, 2, 3, 4, 5}:
            raise ValueError("rating must be between 1 and 5.")
        row = AgentFeedback(
            run_id=run.id,
            tenant_id=tenant_id,
            user_id=user_id,
            verdict=verdict,
            rating=rating,
            reason_tags_json=reason_tags,
            comment=comment,
            correction_json=correction_json,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        if verdict in self.NEGATIVE_VERDICTS or (rating is not None and rating <= 2):
            review = AgentQualityReview(
                run_id=run.id,
                feedback_id=row.id,
                tenant_id=run.tenant_id,
                trigger_type="negative_user_feedback",
                severity="high" if verdict == "unsafe" else "medium",
                checks_json={
                    "verdict": verdict,
                    "rating": rating,
                    "reason_tags": reason_tags,
                    "has_correction": bool(correction_json),
                },
            )
            db.add(review)
            db.commit()
        if correction_json and run.profile_id:
            CareerMemoryService().upsert(
                db,
                memory_type="correction",
                memory_key=f"feedback:{row.id}",
                value_json=correction_json,
                tenant_id=tenant_id,
                user_id=user_id,
                profile_id=run.profile_id,
                source_type="explicit_user_feedback",
                source_run_id=run.id,
            )
        return row
