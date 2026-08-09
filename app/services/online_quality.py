from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.tools import get_agent_tool
from app.core.config import Settings, get_settings
from app.models.entities import AgentArtifact, AgentQualityReview, AgentRun, AgentStep


class OnlineAgentQualityService:
    """Deterministic production monitor that routes suspicious runs to review."""

    VERSION = "careeragent-online-quality-v1"

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def assess_and_route(self, db: Session, *, run: AgentRun) -> dict[str, Any]:
        steps = db.query(AgentStep).filter(AgentStep.run_id == run.id).order_by(AgentStep.id.asc()).all()
        artifacts = (
            db.query(AgentArtifact)
            .filter(AgentArtifact.run_id == run.id)
            .order_by(AgentArtifact.id.asc())
            .all()
        )
        failed_steps = [step.step_name for step in steps if step.status == "failed"]
        unknown_tools = sorted(
            {
                str(step.tool_name)
                for step in steps
                if step.tool_name and not self._registered(str(step.tool_name))
            }
        )
        completion = next(
            (
                artifact.artifact_json
                for artifact in reversed(artifacts)
                if artifact.artifact_type
                in {"completion_verification", "natural_language_completion_verification"}
            ),
            None,
        )
        retrieval_failures = self._retrieval_failures(artifacts)
        checks = {
            "terminal_status": run.status,
            "step_count": len(steps),
            "failed_steps": failed_steps,
            "unknown_tools": unknown_tools,
            "completion_gate_present": completion is not None,
            "completion_gate_passed": bool((completion or {}).get("passed")),
            "retrieval_quality_failures": retrieval_failures,
            "error_envelope_present": bool((run.output_json or {}).get("error_envelope")),
        }

        deductions = 0.0
        if run.status == "failed":
            deductions += 0.7
        if failed_steps:
            deductions += min(0.3, len(failed_steps) * 0.1)
        if unknown_tools:
            deductions += 0.25
        if run.status == "completed" and completion is None:
            deductions += 0.2
        if completion is not None and not bool(completion.get("passed")):
            deductions += 0.5
        if retrieval_failures:
            deductions += min(0.3, len(retrieval_failures) * 0.1)
        score = round(max(0.0, 1.0 - deductions), 4)
        normal_interrupt = run.status in {"waiting_for_confirmation", "cancelled", "withdrawn"}
        review_required = not normal_interrupt and score < self.settings.agent_online_quality_min_score
        report = {
            "version": self.VERSION,
            "score": score,
            "threshold": self.settings.agent_online_quality_min_score,
            "decision": "review_required" if review_required else "pass",
            "checks": checks,
            "llm_judge_used": False,
        }
        if review_required and not self._review_exists(db, run.id, "runtime_quality_gate"):
            severity = "high" if run.status == "failed" or score < 0.4 else "medium"
            db.add(
                AgentQualityReview(
                    run_id=run.id,
                    tenant_id=run.tenant_id,
                    trigger_type="runtime_quality_gate",
                    severity=severity,
                    checks_json=report,
                )
            )
            db.commit()
        return report

    @staticmethod
    def _registered(tool_name: str) -> bool:
        try:
            get_agent_tool(tool_name)
            return True
        except KeyError:
            return False

    @staticmethod
    def _review_exists(db: Session, run_id: int, trigger_type: str) -> bool:
        return (
            db.query(AgentQualityReview)
            .filter(
                AgentQualityReview.run_id == run_id,
                AgentQualityReview.trigger_type == trigger_type,
                AgentQualityReview.status == "open",
            )
            .first()
            is not None
        )

    @staticmethod
    def _retrieval_failures(artifacts: list[AgentArtifact]) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for artifact in artifacts:
            payload = artifact.artifact_json or {}
            candidates: list[dict[str, Any]] = []
            if isinstance(payload, dict):
                if isinstance(payload.get("retrieval_quality"), dict):
                    candidates.append(payload["retrieval_quality"])
                tailor = payload.get("tailor")
                if isinstance(tailor, dict) and isinstance(tailor.get("retrieval_quality"), dict):
                    candidates.append(tailor["retrieval_quality"])
            for candidate in candidates:
                if candidate and candidate.get("passed") is False:
                    failures.append(
                        {
                            "artifact_type": artifact.artifact_type,
                            "reason_codes": candidate.get("reason_codes") or [],
                        }
                    )
        return failures
