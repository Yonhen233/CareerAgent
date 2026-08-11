from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.entities import AgentArtifact, AgentRun, HttpRequestMetric


class SLOService:
    def __init__(self, settings: Settings | None = None, policy_path: Path | None = None) -> None:
        self.settings = settings or get_settings()
        path = policy_path or self.settings.base_path / "evals" / "slo_policy.json"
        self.policy = json.loads(path.read_text(encoding="utf-8"))

    def report(
        self,
        db: Session,
        *,
        window_days: int = 30,
        traffic_class: str = "real",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if window_days not in set(self.policy["measurement_windows_days"]):
            raise ValueError(f"Unsupported SLO window: {window_days} days")
        if traffic_class not in set(self.policy["traffic_classes"]):
            raise ValueError(f"Unsupported traffic class: {traffic_class}")
        measured_at = now or datetime.now(timezone.utc)
        configured_start = datetime.fromisoformat(self.policy["tracking_started_at"])
        started_at = max(measured_at - timedelta(days=window_days), configured_start)
        http_rows = (
            db.query(HttpRequestMetric)
            .filter(
                HttpRequestMetric.created_at >= started_at,
                HttpRequestMetric.traffic_class == traffic_class,
            )
            .all()
        )
        route_prefixes = self.policy["objectives"]["user_api_availability"]["route_prefixes"]
        user_rows = [row for row in http_rows if any(row.route_template.startswith(p) for p in route_prefixes)]
        window_agent_rows = db.query(AgentRun).filter(AgentRun.created_at >= started_at).all()
        agent_rows = [
            row
            for row in window_agent_rows
            if (row.input_json or {}).get("_traffic_class", "real") == traffic_class
        ]
        objectives = [
            self._ratio_objective(
                "user_api_availability",
                sum(1 for row in user_rows if row.status_code < 500),
                len(user_rows),
            ),
            self._latency_objective(
                "user_api_latency_p95_ms",
                [row.latency_ms for row in user_rows if row.status_code < 500],
            ),
            self._agent_terminal_objective(agent_rows),
            self._latency_objective(
                "agent_latency_p95_ms",
                [row.latency_ms for row in agent_rows if row.status in {"completed", "waiting_for_confirmation"}],
            ),
            self._completion_integrity_objective(db, agent_rows),
        ]
        evaluated = [item for item in objectives if item["status"] != "insufficient_data"]
        has_insufficient = any(item["status"] == "insufficient_data" for item in objectives)
        has_breach = any(item["status"] == "breached" for item in objectives)
        return {
            "policy_version": self.policy["version"],
            "traffic_class": traffic_class,
            "window_days": window_days,
            "window_started_at": started_at.isoformat(),
            "measured_at": measured_at.isoformat(),
            "sample_counts": {"http_user_api": len(user_rows), "agent_runs": len(agent_rows)},
            "agent_runs_by_task": self._agent_breakdown(agent_rows),
            "status": "breached" if has_breach else "insufficient_data" if not evaluated else "partial" if has_insufficient else "met",
            "objectives": objectives,
            "interpretation": (
                "真实流量和合成探针分开计算；合成结果用于发布前验证，不能替代线上 SLO。"
            ),
        }

    @staticmethod
    def _agent_breakdown(rows: list[AgentRun]) -> dict[str, dict[str, int]]:
        output: dict[str, dict[str, int]] = {}
        for row in rows:
            task = output.setdefault(row.task_type, {})
            task[row.status] = task.get(row.status, 0) + 1
        return output

    def _agent_terminal_objective(self, rows: list[AgentRun]) -> dict[str, Any]:
        eligible = [row for row in rows if row.status not in {"cancelled", "withdrawn"}]
        good = sum(1 for row in eligible if row.status in {"completed", "waiting_for_confirmation"})
        return self._ratio_objective("agent_valid_terminal_rate", good, len(eligible))

    def _completion_integrity_objective(self, db: Session, rows: list[AgentRun]) -> dict[str, Any]:
        completed_ids = [row.id for row in rows if row.status == "completed"]
        artifacts = (
            db.query(AgentArtifact)
            .filter(
                AgentArtifact.run_id.in_(completed_ids),
                AgentArtifact.artifact_type.in_(
                    {"completion_verification", "natural_language_completion_verification"}
                ),
            )
            .all()
            if completed_ids
            else []
        )
        passed_ids = {row.run_id for row in artifacts if bool((row.artifact_json or {}).get("passed"))}
        return self._ratio_objective("completion_integrity_rate", len(passed_ids), len(completed_ids))

    def _ratio_objective(self, name: str, good: int, total: int) -> dict[str, Any]:
        policy = self.policy["objectives"][name]
        target = float(policy["target"])
        minimum = int(policy["minimum_samples"])
        value = good / total if total else 0.0
        allowed_bad = math.floor(total * (1.0 - target))
        consumed_bad = total - good
        return {
            "name": name,
            "description": policy["description"],
            "status": "insufficient_data" if total < minimum else "met" if value >= target else "breached",
            "value": round(value, 6),
            "target": target,
            "good_samples": good,
            "total_samples": total,
            "minimum_samples": minimum,
            "error_budget": {
                "allowed_bad_samples": allowed_bad,
                "consumed_bad_samples": consumed_bad,
                "remaining_bad_samples": allowed_bad - consumed_bad,
            },
            "wilson_95_lower_bound": round(self._wilson_lower_bound(good, total), 6),
        }

    def _latency_objective(self, name: str, values: Iterable[float]) -> dict[str, Any]:
        policy = self.policy["objectives"][name]
        rows = sorted(max(float(value), 0.0) for value in values)
        minimum = int(policy["minimum_samples"])
        p95 = self._percentile(rows, 0.95)
        target = float(policy["target_max"])
        return {
            "name": name,
            "description": policy["description"],
            "status": "insufficient_data" if len(rows) < minimum else "met" if p95 <= target else "breached",
            "value": round(p95, 3),
            "target_max": target,
            "total_samples": len(rows),
            "minimum_samples": minimum,
        }

    @staticmethod
    def _percentile(values: list[float], quantile: float) -> float:
        if not values:
            return 0.0
        index = max(math.ceil(len(values) * quantile) - 1, 0)
        return values[index]

    @staticmethod
    def _wilson_lower_bound(good: int, total: int, z: float = 1.96) -> float:
        if total <= 0:
            return 0.0
        proportion = good / total
        denominator = 1 + z * z / total
        centre = proportion + z * z / (2 * total)
        margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total)
        return max((centre - margin) / denominator, 0.0)
