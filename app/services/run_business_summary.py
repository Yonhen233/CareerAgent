from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.tools import get_agent_tool
from app.models.entities import (
    AgentApproval,
    AgentArtifact,
    AgentRun,
    AgentStep,
    Job,
    ResumeVersion,
)


TASK_LABELS = {
    "find_jobs_for_profile": "搜索并匹配岗位",
    "tailor_resume_for_job": "定制岗位简历",
    "quick_apply": "准备投递材料",
    "prepare_interview_for_job": "生成面试准备",
    "full_career_flow": "完整求职流程",
    "natural_language_request": "自然语言求职任务",
}


class RunBusinessSummaryService:
    """Turn low-level trace data into a user-facing, evidence-backed run report."""

    def build(
        self,
        db: Session,
        *,
        run: AgentRun,
        output_json: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        output = dict(output_json or run.output_json or {})
        output.pop("business_summary", None)
        effective_status = status or run.status
        plan = output.get("execution_plan") or {}
        steps = (
            db.query(AgentStep)
            .filter(AgentStep.run_id == run.id)
            .order_by(AgentStep.id.asc())
            .all()
        )
        approvals = (
            db.query(AgentApproval)
            .filter(AgentApproval.run_id == run.id)
            .order_by(AgentApproval.id.asc())
            .all()
        )
        artifacts = (
            db.query(AgentArtifact)
            .filter(AgentArtifact.run_id == run.id)
            .order_by(AgentArtifact.id.asc())
            .all()
        )

        selected_job = self._selected_job(db, run, output, artifacts)
        resume_version = self._resume_version(db, output, artifacts)
        verification = self._verification(output, resume_version)
        alignment = resume_version.keyword_alignment_json if resume_version else {}
        repair = alignment.get("react_repair") if isinstance(alignment, dict) else {}
        repair = repair if isinstance(repair, dict) else {}
        covered, missing = self._coverage_terms(verification, alignment, selected_job)
        denominator = len(set(covered) | set(missing))
        evidence_coverage = round(len(set(covered)) / denominator, 4) if denominator else None

        completed_tools = [step for step in steps if step.status == "completed"]
        failed_tools = [step for step in steps if step.status == "failed"]
        tool_names = list(dict.fromkeys(step.tool_name for step in steps if step.tool_name))
        tool_success_rate = round(len(completed_tools) / len(steps), 4) if steps else None
        high_risk_tools = []
        for tool_name in tool_names:
            try:
                policy = get_agent_tool(tool_name)
            except KeyError:
                continue
            if policy.risk_level == "high":
                high_risk_tools.append(tool_name)

        approval_rows = [
            {
                "approval_id": row.id,
                "action_type": row.action_type,
                "status": row.status,
                "decided_by_user_id": row.decided_by_user_id,
            }
            for row in approvals
        ]
        outbound_results = self._outbound_results(artifacts)
        approved_actions = {row.action_type for row in approvals if row.status == "approved"}
        approval_bypass_detected = any(
            item["action_type"] not in approved_actions for item in outbound_results
        )
        approval_status = self._approval_status(approvals, output, high_risk_tools)
        repair_attempts = repair.get("attempts") if isinstance(repair.get("attempts"), list) else []
        trigger_issues = (
            repair.get("trigger_issue_types")
            if isinstance(repair.get("trigger_issue_types"), list)
            else []
        )
        result_ids = self._result_ids(output, resume_version)
        selected_skills = list(plan.get("skills") or [])
        tool_permission = plan.get("tool_permission_validation") or {
            "passed": True,
            "checked_tool_count": len(tool_names),
            "violations": [],
        }
        application_validation = self._application_validation(output)

        summary = {
            "schema_version": "1.0",
            "run_id": run.id,
            "task_type": run.task_type,
            "task_label": TASK_LABELS.get(run.task_type, run.task_type),
            "status": effective_status,
            "headline": self._headline(effective_status, selected_job, output),
            "selected_job": selected_job,
            "skills_used": selected_skills,
            "tools_used": tool_names,
            "metrics": {
                "match_score": self._number(
                    self._first_defined(
                        selected_job.get("overall_score"),
                        output.get("overall_score"),
                        (output.get("tailor") or {}).get("overall_score"),
                    )
                ),
                "matched_skill_count": len(covered),
                "missing_skill_count": len(missing),
                "evidence_coverage": evidence_coverage,
                "source_evidence_count": len(resume_version.source_evidence_json or [])
                if resume_version
                else 0,
                "guardrail_passed": verification.get("passed"),
                "guardrail_risk_level": verification.get("risk_level"),
                "unsupported_claim_count": int(verification.get("hallucination_count") or 0),
                "forbidden_claim_block_count": len(trigger_issues),
                "repair_count": len(repair_attempts),
                "tool_call_count": len(steps),
                "tool_success_rate": tool_success_rate,
                "failed_tool_count": len(failed_tools),
                "idempotency_reuse_count": self._count_key(output, "idempotency_reused", True),
                "latency_ms": run.latency_ms,
            },
            "routing_layer": {
                "selected_skills": selected_skills,
                "selected_subagents": [
                    item.get("name") for item in plan.get("subagents", []) if isinstance(item, dict)
                ],
                "tool_permission_validation": tool_permission,
            },
            "process_layer": {
                "tool_calls": len(steps),
                "completed_tools": len(completed_tools),
                "failed_tools": len(failed_tools),
                "tool_success_rate": tool_success_rate,
                "repair_count": len(repair_attempts),
                "idempotency_reuse_count": self._count_key(output, "idempotency_reused", True),
                "latency_ms": run.latency_ms,
            },
            "result_layer": {
                "matched_skills": covered,
                "missing_skills": missing,
                "evidence_coverage": evidence_coverage,
                "verification": {
                    "passed": verification.get("passed"),
                    "risk_level": verification.get("risk_level"),
                    "unsupported_claim_count": int(verification.get("hallucination_count") or 0),
                    "current_issue_count": len(verification.get("issues") or []),
                    "forbidden_claim_block_count": len(trigger_issues),
                },
                "application_validation": application_validation,
                "result_ids": result_ids,
            },
            "side_effect_layer": {
                "high_risk_tools": high_risk_tools,
                "approval_required": bool(high_risk_tools or approvals or output.get("requires_confirmation")),
                "approval_status": approval_status,
                "approvals": approval_rows,
                "approval_bypass_detected": approval_bypass_detected,
                "outbound_results": outbound_results,
            },
            "links": self._links(run.id, result_ids, selected_job),
        }
        return summary

    def _selected_job(
        self,
        db: Session,
        run: AgentRun,
        output: dict[str, Any],
        artifacts: list[AgentArtifact],
    ) -> dict[str, Any]:
        selected = output.get("selected_job")
        if not isinstance(selected, dict) or not selected:
            matches = output.get("matches")
            if isinstance(matches, list) and matches and isinstance(matches[0], dict):
                selected = matches[0]
        if not isinstance(selected, dict) or not selected:
            selected_artifact = next(
                (item for item in reversed(artifacts) if item.artifact_type == "selected_job"),
                None,
            )
            if selected_artifact:
                selected = (selected_artifact.artifact_json or {}).get("selected_job")
        if not isinstance(selected, dict):
            selected = {}

        interrupt_value = self._interrupt_value(output)
        job_id = (
            selected.get("job_id")
            or output.get("job_id")
            or interrupt_value.get("job_id")
            or run.job_id
        )
        job = db.query(Job).filter(Job.id == int(job_id)).first() if job_id else None
        if job:
            selected = {
                "job_id": job.id,
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "apply_url": job.apply_url,
                **selected,
            }
        return selected

    def _resume_version(
        self,
        db: Session,
        output: dict[str, Any],
        artifacts: list[AgentArtifact],
    ) -> ResumeVersion | None:
        tailor = output.get("tailor") if isinstance(output.get("tailor"), dict) else {}
        interrupt_value = self._interrupt_value(output)
        resume_version_id = (
            output.get("resume_version_id")
            or tailor.get("resume_version_id")
            or interrupt_value.get("resume_version_id")
        )
        if not resume_version_id:
            tailored_artifact = next(
                (item for item in reversed(artifacts) if item.artifact_type == "tailored_resume"),
                None,
            )
            if tailored_artifact:
                resume_version_id = (tailored_artifact.artifact_json or {}).get("resume_version_id")
        if not resume_version_id:
            return None
        return db.query(ResumeVersion).filter(ResumeVersion.id == int(resume_version_id)).first()

    def _verification(
        self,
        output: dict[str, Any],
        resume_version: ResumeVersion | None,
    ) -> dict[str, Any]:
        if resume_version and isinstance(resume_version.verification_json, dict):
            return resume_version.verification_json
        verification = output.get("verification")
        if not isinstance(verification, dict):
            verification = (output.get("tailor") or {}).get("verification")
        return verification if isinstance(verification, dict) else {}

    def _coverage_terms(
        self,
        verification: dict[str, Any],
        alignment: dict[str, Any],
        selected_job: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        covered = verification.get("covered_required_skills")
        missing = verification.get("missing_required_skills")
        if not isinstance(covered, list):
            covered = alignment.get("covered") if isinstance(alignment.get("covered"), list) else []
        if not isinstance(missing, list):
            missing = alignment.get("missing") if isinstance(alignment.get("missing"), list) else []
        if not covered:
            covered = selected_job.get("matched_skills") or []
        if not missing:
            missing = selected_job.get("missing_skills") or []
        return self._unique_strings(covered), self._unique_strings(missing)

    def _application_validation(self, output: dict[str, Any]) -> dict[str, Any]:
        application = output.get("application") if isinstance(output.get("application"), dict) else output
        validation = application.get("packet_validation") if isinstance(application, dict) else {}
        return validation if isinstance(validation, dict) else {}

    def _result_ids(
        self,
        output: dict[str, Any],
        resume_version: ResumeVersion | None,
    ) -> dict[str, int]:
        tailor = output.get("tailor") if isinstance(output.get("tailor"), dict) else {}
        application = output.get("application") if isinstance(output.get("application"), dict) else output
        interview = (
            output.get("interview_prep")
            if isinstance(output.get("interview_prep"), dict)
            else output
        )
        candidates = {
            "resume_version_id": (resume_version.id if resume_version else None)
            or output.get("resume_version_id")
            or tailor.get("resume_version_id"),
            "application_id": application.get("application_id"),
            "interview_prep_id": interview.get("interview_prep_id"),
        }
        return {key: int(value) for key, value in candidates.items() if value}

    def _outbound_results(self, artifacts: list[AgentArtifact]) -> list[dict[str, Any]]:
        results = []
        for artifact in artifacts:
            if artifact.artifact_type not in {
                "browser_apply_result",
                "email_draft_result",
                "email_send_result",
            }:
                continue
            action_type = artifact.artifact_type.removesuffix("_result")
            payload = artifact.artifact_json or {}
            results.append(
                {
                    "artifact_id": artifact.id,
                    "action_type": action_type,
                    "status": payload.get("status"),
                    "tool_result": payload.get("tool_result"),
                }
            )
        return results

    def _approval_status(
        self,
        approvals: list[AgentApproval],
        output: dict[str, Any],
        high_risk_tools: list[str],
    ) -> str:
        if approvals:
            statuses = [row.status for row in approvals]
            if "pending" in statuses:
                return "pending"
            if "rejected" in statuses:
                return "rejected"
            if all(status == "approved" for status in statuses):
                return "approved"
            return statuses[-1]
        if output.get("requires_confirmation"):
            return "pending"
        return "not_required" if not high_risk_tools else "missing"

    def _interrupt_value(self, output: dict[str, Any]) -> dict[str, Any]:
        interrupts = output.get("interrupts")
        if not isinstance(interrupts, list) or not interrupts:
            return {}
        first = interrupts[0] if isinstance(interrupts[0], dict) else {}
        value = first.get("value")
        return value if isinstance(value, dict) else {}

    def _links(
        self,
        run_id: int,
        result_ids: dict[str, int],
        selected_job: dict[str, Any],
    ) -> dict[str, str]:
        links = {"trace": f"/ui/agent-runs?run_id={run_id}"}
        if selected_job.get("job_id"):
            links["job"] = f"/jobs/{selected_job['job_id']}/html"
        if result_ids.get("resume_version_id"):
            links["resume"] = f"/resumes/{result_ids['resume_version_id']}/html"
        if result_ids.get("application_id"):
            links["application"] = "/ui/applications"
        if result_ids.get("interview_prep_id"):
            links["interview_prep"] = "/ui/prep"
        return links

    def _headline(
        self,
        status: str,
        selected_job: dict[str, Any],
        output: dict[str, Any],
    ) -> str:
        job_text = " ".join(
            item for item in [selected_job.get("company"), selected_job.get("title")] if item
        ).strip()
        if status == "completed":
            return f"已完成 {job_text or '本次求职任务'}"
        if status == "waiting_for_confirmation" or output.get("requires_confirmation"):
            confirmation = output
            nested_confirmation = (output.get("result_json") or {}).get("requires_confirmation")
            if isinstance(nested_confirmation, dict):
                confirmation = nested_confirmation
            interrupt = self._interrupt_value(confirmation)
            confirmation_type = confirmation.get("confirmation_type")
            if confirmation_type == "job_selection" or interrupt.get("kind") == "job_selection":
                match_count = len(interrupt.get("matches") or [])
                return f"已找到 {match_count} 个候选岗位，等待你选择"
            return f"{job_text or '投递材料'}已准备，等待你的确认"
        if status == "failed":
            return f"{job_text or '本次任务'}处理失败，可在 Trace 中定位原因"
        if status == "cancelled":
            return "本次求职任务已取消"
        return f"{job_text or '本次求职任务'}正在处理"

    def _count_key(self, value: Any, key: str, expected: Any) -> int:
        if isinstance(value, dict):
            return int(value.get(key) == expected) + sum(
                self._count_key(item, key, expected) for item in value.values()
            )
        if isinstance(value, list):
            return sum(self._count_key(item, key, expected) for item in value)
        return 0

    def _unique_strings(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

    def _number(self, value: Any) -> float | None:
        try:
            return round(float(value), 2) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _first_defined(self, *values: Any) -> Any:
        return next((value for value in values if value is not None), None)
