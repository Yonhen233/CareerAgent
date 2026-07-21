from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import LLMCallLog


class LLMUsageService:
    """Aggregate provider-reported LLM usage for the operations console."""

    def summarize(
        self,
        db: Session,
        *,
        hours: int = 24,
        since_id: int | None = None,
        workflow: str | None = None,
        workflow_run_id: str | None = None,
    ) -> dict[str, Any]:
        end_at = datetime.now(timezone.utc)
        start_at = end_at - timedelta(hours=hours)
        query = db.query(LLMCallLog).filter(LLMCallLog.created_at >= start_at)
        if since_id is not None:
            query = query.filter(LLMCallLog.id > since_id)
        rows = query.order_by(LLMCallLog.id.asc()).all()
        if workflow:
            rows = [row for row in rows if str((row.context_json or {}).get("workflow") or "") == workflow]
        if workflow_run_id:
            rows = [
                row
                for row in rows
                if str((row.context_json or {}).get("workflow_run_id") or "") == workflow_run_id
            ]

        return {
            "window": {
                "hours": hours,
                "since_id": since_id,
                "workflow": workflow,
                "workflow_run_id": workflow_run_id,
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
            },
            "summary": self._aggregate(rows),
            "by_model": self._group(rows, lambda row: row.model or "unknown"),
            "by_workflow": self._group(
                rows,
                lambda row: str((row.context_json or {}).get("workflow") or "unclassified"),
            ),
            "by_workflow_run": self._group(
                rows,
                lambda row: str((row.context_json or {}).get("workflow_run_id") or "unclassified"),
                limit=20,
            ),
            "by_trace": self._group(rows, lambda row: row.trace_name or "unknown", limit=20),
        }

    def _group(self, rows: list[LLMCallLog], key_fn, *, limit: int = 12) -> list[dict[str, Any]]:
        groups: dict[str, list[LLMCallLog]] = defaultdict(list)
        for row in rows:
            groups[key_fn(row)].append(row)
        payload = [{"key": key, **self._aggregate(group)} for key, group in groups.items()]
        payload.sort(key=lambda item: (item["total_tokens"], item["log_count"]), reverse=True)
        return payload[:limit]

    @staticmethod
    def _aggregate(rows: list[LLMCallLog]) -> dict[str, Any]:
        completed = [row for row in rows if row.status == "completed"]
        reported = [row for row in completed if int(row.total_tokens or 0) > 0]
        prompt_tokens = sum(int(row.prompt_tokens or 0) for row in reported)
        completion_tokens = sum(int(row.completion_tokens or 0) for row in reported)
        total_tokens = sum(int(row.total_tokens or 0) for row in reported)
        completed_count = len(completed)
        return {
            "log_count": len(rows),
            "completed_calls": completed_count,
            "non_completed_calls": len(rows) - completed_count,
            "provider_usage_calls": len(reported),
            "missing_usage_calls": completed_count - len(reported),
            "usage_coverage_rate": round(len(reported) / completed_count, 4) if completed_count else 0.0,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "prompt_chars": sum(int(row.prompt_chars or 0) for row in rows),
            "response_chars": sum(int(row.response_chars or 0) for row in rows),
            "total_latency_ms": sum(int(row.latency_ms or 0) for row in rows),
            "avg_tokens_per_reported_call": round(total_tokens / len(reported), 2) if reported else 0.0,
            "latest_log_id": max((int(row.id) for row in rows), default=None),
        }
