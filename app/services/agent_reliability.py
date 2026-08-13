from __future__ import annotations

import fnmatch
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import (
    AgentApproval,
    AgentArtifact,
    AgentRun,
    AgentStep,
    Application,
    InterviewPrep,
    Job,
    MatchResult,
    Profile,
    ResumeVersion,
)


TASK_CONTRACT_VERSION = "careeragent-task-contract-v3"
COMPLETION_GATE_VERSION = "careeragent-completion-gate-v2"


class AgentTaskIncompleteError(RuntimeError):
    """Raised when a graph reaches its terminal path without satisfying its task contract."""


class AgentExecutionBudgetExceeded(RuntimeError):
    """Raised before an Agent repeats work or exceeds its bounded execution policy."""


@dataclass(frozen=True)
class TaskPolicy:
    required_goals: tuple[str, ...]
    required_artifacts: tuple[str, ...]
    required_steps: tuple[str, ...]
    ordered_steps: tuple[tuple[str, str], ...]
    allowed_tools: tuple[str, ...]


COMMON_ALLOWED_TOOLS = (
    "LangGraph.AgentPlanner",
    "profile_repository.load_profile",
    "job_repository.load_job",
    "job_search.search_jobs",
    "matcher.match_job",
    "matcher.enforce_fit_gate",
    "resume_tailor.tailor_resume",
    "application.create_quick_apply_packet",
    "interview_prep.generate_packet",
)


TASK_POLICIES: dict[str, TaskPolicy] = {
    "find_jobs_for_profile": TaskPolicy(
        required_goals=("profile_loaded", "jobs_retrieved", "jobs_ranked", "result_exposed"),
        required_artifacts=("execution_plan", "ranked_jobs"),
        required_steps=("plan_task", "load_profile", "search_jobs", "match_job_*"),
        ordered_steps=(("load_profile", "search_jobs"), ("search_jobs", "match_job_*")),
        allowed_tools=(
            "LangGraph.AgentPlanner",
            "profile_repository.load_profile",
            "job_search.search_jobs",
            "matcher.match_job",
        ),
    ),
    "tailor_resume_for_job": TaskPolicy(
        required_goals=(
            "profile_loaded",
            "job_loaded",
            "match_analyzed",
            "resume_tailored",
            "resume_verified",
            "result_exposed",
        ),
        required_artifacts=("execution_plan", "tailored_resume"),
        required_steps=("plan_task", "load_profile", "load_job", "match_job", "tailor_resume_with_rag"),
        ordered_steps=(
            ("load_profile", "load_job"),
            ("load_job", "match_job"),
            ("match_job", "tailor_resume_with_rag"),
        ),
        allowed_tools=(
            "LangGraph.AgentPlanner",
            "profile_repository.load_profile",
            "job_repository.load_job",
            "matcher.match_job",
            "matcher.enforce_fit_gate",
            "resume_tailor.tailor_resume",
        ),
    ),
    "quick_apply": TaskPolicy(
        required_goals=(
            "profile_loaded",
            "job_loaded",
            "match_analyzed",
            "fit_gate_passed",
            "resume_available",
            "application_approved",
            "application_packet_created",
            "application_packet_validated",
            "result_exposed",
        ),
        required_artifacts=("execution_plan", "fit_gate", "application_packet"),
        required_steps=(
            "plan_task",
            "load_profile",
            "load_job",
            "match_job",
            "fit_gate",
            "create_application_packet",
        ),
        ordered_steps=(
            ("load_job", "match_job"),
            ("match_job", "fit_gate"),
            ("fit_gate", "create_application_packet"),
        ),
        allowed_tools=(
            "LangGraph.AgentPlanner",
            "profile_repository.load_profile",
            "job_repository.load_job",
            "matcher.match_job",
            "matcher.enforce_fit_gate",
            "resume_tailor.tailor_resume",
            "application.create_quick_apply_packet",
        ),
    ),
    "prepare_interview_for_job": TaskPolicy(
        required_goals=(
            "profile_loaded",
            "job_loaded",
            "match_analyzed",
            "interview_packet_created",
            "interview_packet_validated",
            "result_exposed",
        ),
        required_artifacts=("execution_plan", "interview_prep"),
        required_steps=("plan_task", "load_profile", "load_job", "match_job", "generate_interview_prep"),
        ordered_steps=(
            ("load_job", "match_job"),
            ("match_job", "generate_interview_prep"),
        ),
        allowed_tools=(
            "LangGraph.AgentPlanner",
            "profile_repository.load_profile",
            "job_repository.load_job",
            "matcher.match_job",
            "interview_prep.generate_packet",
        ),
    ),
    "full_career_flow": TaskPolicy(
        required_goals=(
            "profile_loaded",
            "job_selected",
            "match_analyzed",
            "resume_tailored",
            "resume_verified",
            "fit_gate_passed",
            "application_approved",
            "application_packet_created",
            "application_packet_validated",
            "interview_packet_created",
            "interview_packet_validated",
            "result_exposed",
        ),
        required_artifacts=(
            "execution_plan",
            "tailored_resume",
            "fit_gate",
            "application_packet",
            "interview_prep",
            "full_career_flow",
        ),
        required_steps=(
            "plan_task",
            "load_profile",
            "load_job",
            "match_job",
            "tailor_resume_with_rag",
            "fit_gate",
            "create_application_packet",
            "generate_interview_prep",
        ),
        ordered_steps=(
            ("load_job", "match_job"),
            ("match_job", "tailor_resume_with_rag"),
            ("tailor_resume_with_rag", "fit_gate"),
            ("fit_gate", "create_application_packet"),
            ("create_application_packet", "generate_interview_prep"),
        ),
        allowed_tools=COMMON_ALLOWED_TOOLS,
    ),
}


class AgentTaskContractService:
    def build_contract(self, task_type: str, request: dict[str, Any]) -> dict[str, Any]:
        policy = self._policy(task_type)
        required_artifacts = list(policy.required_artifacts)
        if task_type == "full_career_flow" and not request.get("job_id"):
            required_artifacts.extend(["ranked_jobs", "selected_job"])
        return {
            "version": TASK_CONTRACT_VERSION,
            "task_type": task_type,
            "required_goals": list(policy.required_goals),
            "required_artifacts": required_artifacts,
            "terminal_states": ["completed", "waiting_for_confirmation", "failed_explicitly"],
            "completion_rule": "Only the completion_gate may mark the task completed.",
            "failure_rule": "Missing goals or invalid trajectory must fail explicitly with evidence.",
            "execution_limits": {
                "graph_shape": "bounded_dag",
                "natural_language_repair_attempts": 1,
                "side_effects_require_idempotency": True,
            },
        }

    def verify(
        self,
        db: Session,
        *,
        run_id: int,
        task_type: str,
        request: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        contract = state.get("task_contract") or self.build_contract(task_type, request)
        artifact_types = self._artifact_types(db, run_id)
        goal_checks = self._goal_checks(db, run_id=run_id, task_type=task_type, state=state)
        goal_ledger = [
            {
                "goal_id": goal,
                "status": "satisfied" if goal_checks.get(goal, False) else "pending",
                "evidence": self._goal_evidence(goal, state, artifact_types),
            }
            for goal in contract["required_goals"]
        ]
        missing_goals = [item["goal_id"] for item in goal_ledger if item["status"] != "satisfied"]
        missing_artifacts = [name for name in contract["required_artifacts"] if name not in artifact_types]
        trajectory = AgentTrajectoryEvaluator().evaluate(
            db,
            run_id=run_id,
            task_type=task_type,
            request=request,
            require_completion_artifact=False,
        )
        integrity_violations = self._state_integrity_violations(task_type, state)
        database_integrity_violations = self._database_integrity_violations(
            db,
            task_type=task_type,
            state=state,
        )
        passed = (
            not missing_goals
            and not missing_artifacts
            and not integrity_violations
            and not database_integrity_violations
            and trajectory["passed"]
        )
        return {
            "version": COMPLETION_GATE_VERSION,
            "passed": passed,
            "terminal_decision": "completed" if passed else "failed_explicitly",
            "task_contract": contract,
            "goal_ledger": goal_ledger,
            "missing_goals": missing_goals,
            "missing_artifacts": missing_artifacts,
            "integrity_violations": integrity_violations,
            "database_integrity_violations": database_integrity_violations,
            "trajectory": trajectory,
            "repair": {
                "eligible": False,
                "reason": (
                    "The bounded workflow already consumed its deterministic path; rerunning the whole graph "
                    "would hide the defect. Retry only the failed task with a new run after inspection."
                )
                if not passed
                else None,
            },
        }

    def verify_natural_language(
        self,
        *,
        plan: dict[str, Any],
        result: dict[str, Any],
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = request or {}
        intent = str(plan.get("intent") or "")
        actions = {str(item) for item in plan.get("actions") or []}
        implicit = {
            "create_profile": "create_profile",
            "update_profile": "create_profile",
            "search_jobs": "search_jobs",
            "tailor_resume": "tailor_resume",
            "quick_apply": "quick_apply",
            "interview_prep": "interview_prep",
            "full_flow": "full_flow",
        }.get(intent)
        if implicit:
            actions.add(implicit)

        waiting = bool(result.get("requires_confirmation"))
        checks: dict[str, bool] = {}
        if "create_profile" in actions:
            checks["create_profile"] = bool((result.get("profile") or {}).get("id"))
        if "search_jobs" in actions:
            checks["search_jobs"] = bool(result.get("matches"))
        if "tailor_resume" in actions:
            checks["tailor_resume"] = bool((result.get("tailor") or {}).get("resume_version_id"))
        if "interview_prep" in actions:
            checks["interview_prep"] = bool((result.get("interview_prep") or {}).get("interview_prep_id"))
        if "quick_apply" in actions:
            checks["quick_apply"] = waiting or bool((result.get("application") or {}).get("application_id"))
        if "full_flow" in actions:
            full_flow = result.get("full_flow") or {}
            checks["full_flow"] = waiting or all(
                bool(full_flow.get(key)) for key in ("tailor", "application", "interview_prep")
            )
        if not checks:
            checks["recognized_result"] = bool(result.get("profile") or result.get("job") or result.get("matches"))
        missing = [name for name, passed in checks.items() if not passed]
        integrity_violations = self._natural_result_integrity_violations(
            request=request,
            result=result,
        )
        return {
            "version": "careeragent-natural-completion-v2",
            "passed": not missing and not integrity_violations,
            "terminal_decision": (
                "waiting_for_confirmation"
                if waiting and not missing and not integrity_violations
                else "completed"
                if not missing and not integrity_violations
                else "repair"
            ),
            "required_actions": sorted(actions),
            "action_checks": checks,
            "missing_actions": missing,
            "integrity_violations": integrity_violations,
        }

    @staticmethod
    def _natural_result_integrity_violations(
        *,
        request: dict[str, Any],
        result: dict[str, Any],
    ) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []

        def compare(section: str, field: str, actual: Any, expected: Any) -> None:
            if expected in {None, ""} or actual in {None, ""}:
                return
            if str(actual) != str(expected):
                violations.append(
                    {
                        "section": section,
                        "field": field,
                        "actual": actual,
                        "expected": expected,
                    }
                )

        profile = result.get("profile") or {}
        job = result.get("job") or {}
        compare("profile", "id", profile.get("id"), request.get("profile_id"))
        compare("job", "id", job.get("id"), request.get("job_id"))

        profile_id = profile.get("id") or request.get("profile_id")
        job_id = job.get("id") or request.get("job_id")
        for section_name in ("tailor", "application", "interview_prep"):
            section = result.get(section_name) or {}
            if not isinstance(section, dict):
                continue
            compare(section_name, "profile_id", section.get("profile_id"), profile_id)
            compare(section_name, "job_id", section.get("job_id"), job_id)

        ranked_job_ids = {
            int(item.get("job_id") or 0)
            for item in (result.get("matches") or [])
            if isinstance(item, dict) and int(item.get("job_id") or 0) > 0
        }
        if ranked_job_ids and job_id and int(job_id) not in ranked_job_ids:
            violations.append(
                {
                    "section": "job",
                    "field": "id",
                    "actual": job_id,
                    "expected": "one of returned matches",
                }
            )
        invalid_child_runs = [
            {"run_id": item.get("run_id"), "status": item.get("status")}
            for item in (result.get("agent_runs") or [])
            if isinstance(item, dict)
            and item.get("status") not in {"completed", "waiting_for_confirmation"}
        ]
        if invalid_child_runs:
            violations.append(
                {
                    "section": "agent_runs",
                    "field": "status",
                    "actual": invalid_child_runs,
                    "expected": ["completed", "waiting_for_confirmation"],
                }
            )
        return violations

    def _goal_checks(
        self,
        db: Session,
        *,
        run_id: int,
        task_type: str,
        state: dict[str, Any],
    ) -> dict[str, bool]:
        output = state.get("output") or {}
        verification = state.get("verification") or (state.get("tailor") or {}).get("verification") or {}
        application = state.get("application") or {}
        interview = state.get("interview_prep") or {}
        confirmation = application.get("human_confirmation") or state.get("human_confirmation") or {}
        approval_ok = bool(confirmation.get("confirmed")) and self._approved_application_action(db, run_id)
        checks = {
            "profile_loaded": bool(state.get("profile_id")),
            "job_loaded": bool(state.get("job_id")),
            "jobs_retrieved": bool(state.get("job_ids")),
            "jobs_ranked": bool(state.get("matches")),
            "job_selected": bool(state.get("selected_job_id") or state.get("job_id")),
            "match_analyzed": bool(state.get("match_result_id")),
            "resume_tailored": bool(state.get("resume_version_id") and state.get("tailor")),
            "resume_verified": verification.get("passed") is True,
            "fit_gate_passed": (state.get("fit_gate") or {}).get("passed") is True,
            "resume_available": bool(state.get("resume_version_id")),
            "application_approved": approval_ok,
            "application_packet_created": bool(application.get("application_id")),
            "application_packet_validated": (application.get("packet_validation") or {}).get("passed") is True,
            "interview_packet_created": bool(interview.get("interview_prep_id")),
            "interview_packet_validated": (interview.get("coverage") or {}).get("passed") is True,
            "result_exposed": self._result_exposed(task_type, output),
        }
        return checks

    def _result_exposed(self, task_type: str, output: dict[str, Any]) -> bool:
        if task_type == "find_jobs_for_profile":
            return bool(output.get("matches"))
        if task_type == "tailor_resume_for_job":
            return bool(output.get("resume_version_id"))
        if task_type == "quick_apply":
            return bool(output.get("application_id"))
        if task_type == "prepare_interview_for_job":
            return bool(output.get("interview_prep_id"))
        if task_type == "full_career_flow":
            return all(bool(output.get(key)) for key in ("tailor", "application", "interview_prep"))
        return False

    def _approved_application_action(self, db: Session, run_id: int) -> bool:
        return (
            db.query(AgentApproval)
            .filter(
                AgentApproval.run_id == run_id,
                AgentApproval.action_type == "application_packet",
                AgentApproval.status == "approved",
            )
            .first()
            is not None
        )

    def _state_integrity_violations(self, task_type: str, state: dict[str, Any]) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        profile_id = int(state.get("profile_id") or 0)
        job_id = int(state.get("job_id") or 0)
        resume_version_id = int(state.get("resume_version_id") or 0)

        def require_equal(section: str, field: str, actual: Any, expected: int) -> None:
            if not expected or actual in {None, ""}:
                return
            try:
                matches = int(actual) == expected
            except (TypeError, ValueError):
                matches = False
            if not matches:
                violations.append(
                    {"section": section, "field": field, "actual": actual, "expected": expected}
                )

        selected = state.get("selected_job") or {}
        tailor = state.get("tailor") or {}
        application = state.get("application") or {}
        interview = state.get("interview_prep") or {}
        output = state.get("output") or {}
        require_equal("selected_job", "job_id", selected.get("job_id"), job_id)
        require_equal("tailor", "profile_id", tailor.get("profile_id"), profile_id)
        require_equal("tailor", "job_id", tailor.get("job_id"), job_id)
        require_equal("tailor", "resume_version_id", tailor.get("resume_version_id"), resume_version_id)
        require_equal("application", "profile_id", application.get("profile_id"), profile_id)
        require_equal("application", "job_id", application.get("job_id"), job_id)
        require_equal("application", "resume_version_id", application.get("resume_version_id"), resume_version_id)
        require_equal("interview_prep", "profile_id", interview.get("profile_id"), profile_id)
        require_equal("interview_prep", "job_id", interview.get("job_id"), job_id)
        if task_type == "full_career_flow" and state.get("matches"):
            ranked_job_ids = {int(item.get("job_id") or 0) for item in state.get("matches") or []}
            if job_id not in ranked_job_ids:
                violations.append(
                    {
                        "section": "selected_job",
                        "field": "job_id",
                        "actual": job_id,
                        "expected": "one of ranked_jobs",
                    }
                )
        if task_type == "tailor_resume_for_job":
            require_equal("output", "resume_version_id", output.get("resume_version_id"), resume_version_id)
        elif task_type == "quick_apply":
            require_equal("output", "application_id", output.get("application_id"), int(application.get("application_id") or 0))
        elif task_type == "prepare_interview_for_job":
            require_equal(
                "output",
                "interview_prep_id",
                output.get("interview_prep_id"),
                int(interview.get("interview_prep_id") or 0),
            )
        return violations

    def _database_integrity_violations(
        self,
        db: Session,
        *,
        task_type: str,
        state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        profile_id = int(state.get("profile_id") or 0)
        job_id = int(state.get("job_id") or 0)

        def missing(entity: str, entity_id: int) -> None:
            violations.append({"entity": entity, "id": entity_id, "reason": "not_found"})

        if profile_id and db.query(Profile).filter(Profile.id == profile_id).first() is None:
            missing("profile", profile_id)
        if job_id and db.query(Job).filter(Job.id == job_id).first() is None:
            missing("job", job_id)

        for item in (state.get("matches") or [])[:50]:
            ranked_job_id = int(item.get("job_id") or 0) if isinstance(item, dict) else 0
            if ranked_job_id and db.query(Job).filter(Job.id == ranked_job_id).first() is None:
                missing("ranked_job", ranked_job_id)

        match_result_id = int(state.get("match_result_id") or 0)
        if match_result_id:
            match = db.query(MatchResult).filter(MatchResult.id == match_result_id).first()
            if match is None:
                missing("match_result", match_result_id)
            elif match.profile_id != profile_id or match.job_id != job_id:
                violations.append(
                    {
                        "entity": "match_result",
                        "id": match_result_id,
                        "reason": "lineage_mismatch",
                        "actual": {"profile_id": match.profile_id, "job_id": match.job_id},
                        "expected": {"profile_id": profile_id, "job_id": job_id},
                    }
                )

        resume_version_id = int(state.get("resume_version_id") or 0)
        if resume_version_id:
            version = db.query(ResumeVersion).filter(ResumeVersion.id == resume_version_id).first()
            if version is None:
                missing("resume_version", resume_version_id)
            elif (
                version.profile_id != profile_id
                or version.job_id != job_id
                or version.lifecycle_status != "active"
            ):
                violations.append(
                    {
                        "entity": "resume_version",
                        "id": resume_version_id,
                        "reason": "lineage_or_lifecycle_mismatch",
                        "actual": {
                            "profile_id": version.profile_id,
                            "job_id": version.job_id,
                            "lifecycle_status": version.lifecycle_status,
                        },
                        "expected": {
                            "profile_id": profile_id,
                            "job_id": job_id,
                            "lifecycle_status": "active",
                        },
                    }
                )

        application_id = int((state.get("application") or {}).get("application_id") or 0)
        if application_id:
            application = db.query(Application).filter(Application.id == application_id).first()
            expected_resume_id = resume_version_id or None
            if application is None:
                missing("application", application_id)
            elif (
                application.profile_id != profile_id
                or application.job_id != job_id
                or application.resume_version_id != expected_resume_id
                or application.withdrawn_at is not None
            ):
                violations.append(
                    {
                        "entity": "application",
                        "id": application_id,
                        "reason": "lineage_or_lifecycle_mismatch",
                        "actual": {
                            "profile_id": application.profile_id,
                            "job_id": application.job_id,
                            "resume_version_id": application.resume_version_id,
                            "withdrawn": application.withdrawn_at is not None,
                        },
                        "expected": {
                            "profile_id": profile_id,
                            "job_id": job_id,
                            "resume_version_id": expected_resume_id,
                            "withdrawn": False,
                        },
                    }
                )

        prep_id = int((state.get("interview_prep") or {}).get("interview_prep_id") or 0)
        if prep_id:
            prep = db.query(InterviewPrep).filter(InterviewPrep.id == prep_id).first()
            if prep is None:
                missing("interview_prep", prep_id)
            elif (
                prep.profile_id != profile_id
                or prep.job_id != job_id
                or prep.lifecycle_status != "active"
            ):
                violations.append(
                    {
                        "entity": "interview_prep",
                        "id": prep_id,
                        "reason": "lineage_or_lifecycle_mismatch",
                        "actual": {
                            "profile_id": prep.profile_id,
                            "job_id": prep.job_id,
                            "lifecycle_status": prep.lifecycle_status,
                        },
                        "expected": {
                            "profile_id": profile_id,
                            "job_id": job_id,
                            "lifecycle_status": "active",
                        },
                    }
                )
        return violations

    def _artifact_types(self, db: Session, run_id: int) -> set[str]:
        return {
            str(row.artifact_type)
            for row in db.query(AgentArtifact).filter(AgentArtifact.run_id == run_id).all()
        }

    def _goal_evidence(self, goal: str, state: dict[str, Any], artifact_types: set[str]) -> dict[str, Any]:
        evidence_keys = {
            "profile_loaded": ["profile_id"],
            "job_loaded": ["job_id"],
            "jobs_retrieved": ["job_ids"],
            "jobs_ranked": ["matches"],
            "job_selected": ["selected_job_id", "job_id"],
            "match_analyzed": ["match_result_id", "overall_score"],
            "resume_tailored": ["resume_version_id"],
            "resume_verified": ["verification"],
            "fit_gate_passed": ["fit_gate"],
            "resume_available": ["resume_version_id"],
            "application_approved": ["application"],
            "application_packet_created": ["application"],
            "application_packet_validated": ["application"],
            "interview_packet_created": ["interview_prep"],
            "interview_packet_validated": ["interview_prep"],
            "result_exposed": ["output"],
        }
        payload: dict[str, Any] = {}
        for key in evidence_keys.get(goal, []):
            value = state.get(key)
            if isinstance(value, list):
                payload[key] = {"count": len(value)}
            elif isinstance(value, dict):
                payload[key] = {"keys": sorted(value.keys())[:20]}
            else:
                payload[key] = value
        payload["artifact_types"] = sorted(artifact_types)
        return payload

    def _policy(self, task_type: str) -> TaskPolicy:
        try:
            return TASK_POLICIES[task_type]
        except KeyError as exc:
            raise ValueError(f"No task contract policy is registered for {task_type}.") from exc


class AgentTrajectoryEvaluator:
    def evaluate(
        self,
        db: Session,
        *,
        run_id: int,
        task_type: str,
        request: dict[str, Any] | None = None,
        require_completion_artifact: bool = True,
    ) -> dict[str, Any]:
        policy = TASK_POLICIES[task_type]
        request = request or {}
        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        expected_policy_block = bool(
            run
            and run.status == "failed"
            and "Fit gate blocked" in str(run.error_message or "")
            and task_type == "quick_apply"
        )
        actual_steps = (
            db.query(AgentStep)
            .filter(AgentStep.run_id == run_id)
            .order_by(AgentStep.id.asc())
            .all()
        )
        inherited_steps: list[AgentStep] = []
        lineage_artifacts = (
            db.query(AgentArtifact)
            .filter(
                AgentArtifact.run_id == run_id,
                AgentArtifact.artifact_type == "checkpoint_inherited_trajectory",
            )
            .order_by(AgentArtifact.id.asc())
            .all()
        )
        for artifact in lineage_artifacts:
            for item in (artifact.artifact_json or {}).get("steps") or []:
                inherited_steps.append(
                    AgentStep(
                        run_id=run_id,
                        step_name=str(item.get("step_name") or ""),
                        tool_name=item.get("tool_name"),
                        status=str(item.get("status") or "completed"),
                        input_json=item.get("input_json") or {},
                        output_json=item.get("output_json") or {},
                        latency_ms=int(item.get("latency_ms") or 0),
                    )
                )
        steps = [*inherited_steps, *actual_steps]
        artifacts = {
            row.artifact_type
            for row in db.query(AgentArtifact).filter(AgentArtifact.run_id == run_id).all()
        }
        names = [step.step_name for step in steps]
        tools = [str(step.tool_name) for step in steps if step.tool_name]

        required_steps = list(policy.required_steps)
        if expected_policy_block:
            required_steps = ["plan_task", "load_profile", "load_job", "match_job", "fit_gate"]
        if task_type == "full_career_flow" and not request.get("job_id"):
            required_steps.extend(["search_jobs", "match_job_*"])
        missing_steps = [pattern for pattern in required_steps if not self._matching_indexes(names, pattern)]
        latest_attempts: dict[tuple[str, str, str], AgentStep] = {}
        for step in steps:
            signature = (
                step.step_name,
                str(step.tool_name or ""),
                json.dumps(step.input_json or {}, ensure_ascii=False, sort_keys=True, default=str),
            )
            latest_attempts[signature] = step
        failed_steps = [
            step.step_name
            for step in latest_attempts.values()
            if step.status != "completed"
            and not (expected_policy_block and step.step_name == "fit_gate")
        ]
        unexpected_tools = [
            tool for tool in tools if not any(fnmatch.fnmatchcase(tool, pattern) for pattern in policy.allowed_tools)
        ]
        order_violations = []
        for before, after in policy.ordered_steps:
            before_indexes = self._matching_indexes(names, before)
            after_indexes = self._matching_indexes(names, after)
            if before_indexes and after_indexes and min(after_indexes) < max(before_indexes):
                order_violations.append({"before": before, "after": after})

        argument_violations = self._argument_violations(steps, request)
        duplicate_violations = self._duplicate_violations(steps)
        approval_violation = None
        if "application.create_quick_apply_packet" in tools:
            approved = (
                db.query(AgentApproval)
                .filter(
                    AgentApproval.run_id == run_id,
                    AgentApproval.action_type == "application_packet",
                    AgentApproval.status == "approved",
                )
                .first()
                is not None
            )
            if not approved:
                approval_violation = "application tool executed without an approved application_packet action"
        policy_block_violation = None
        if expected_policy_block and "application.create_quick_apply_packet" in tools:
            policy_block_violation = "application tool executed after fit gate rejected the run"
        missing_completion_artifact = (
            require_completion_artifact
            and not expected_policy_block
            and "completion_verification" not in artifacts
        )
        passed = not any(
            [
                missing_steps,
                failed_steps,
                unexpected_tools,
                order_violations,
                argument_violations,
                duplicate_violations,
                approval_violation,
                policy_block_violation,
                missing_completion_artifact,
            ]
        )
        return {
            "version": "careeragent-trajectory-eval-v2",
            "passed": passed,
            "actual_steps": names,
            "actual_tools": tools,
            "inherited_step_count": len(inherited_steps),
            "missing_steps": missing_steps,
            "failed_steps": failed_steps,
            "unexpected_tools": unexpected_tools,
            "order_violations": order_violations,
            "argument_violations": argument_violations,
            "duplicate_violations": duplicate_violations,
            "approval_violation": approval_violation,
            "expected_policy_block": expected_policy_block,
            "policy_block_violation": policy_block_violation,
            "missing_completion_artifact": missing_completion_artifact,
        }

    def _argument_violations(self, steps: list[AgentStep], request: dict[str, Any]) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        profile_id = request.get("profile_id")
        requested_job_id = request.get("job_id")
        for step in steps:
            payload = step.input_json or {}
            if step.tool_name == "profile_repository.load_profile" and profile_id:
                if int(payload.get("profile_id") or 0) != int(profile_id):
                    violations.append({"step": step.step_name, "field": "profile_id", "reason": "request_mismatch"})
            if step.tool_name in {
                "matcher.match_job",
                "matcher.enforce_fit_gate",
                "resume_tailor.tailor_resume",
                "application.create_quick_apply_packet",
                "interview_prep.generate_packet",
            }:
                if int(payload.get("profile_id") or 0) <= 0:
                    violations.append({"step": step.step_name, "field": "profile_id", "reason": "missing"})
                if int(payload.get("job_id") or 0) <= 0:
                    violations.append({"step": step.step_name, "field": "job_id", "reason": "missing"})
            if step.tool_name == "job_repository.load_job" and requested_job_id:
                if int(payload.get("job_id") or 0) != int(requested_job_id):
                    violations.append({"step": step.step_name, "field": "job_id", "reason": "request_mismatch"})
            if step.tool_name == "application.create_quick_apply_packet":
                if int(payload.get("resume_version_id") or 0) <= 0:
                    violations.append({"step": step.step_name, "field": "resume_version_id", "reason": "missing"})
        return violations

    def _duplicate_violations(self, steps: list[AgentStep]) -> list[dict[str, Any]]:
        signatures = Counter(
            (
                str(step.tool_name or ""),
                json.dumps(step.input_json or {}, ensure_ascii=False, sort_keys=True, default=str),
            )
            for step in steps
        )
        return [
            {"tool_name": tool, "input_json": json.loads(payload), "count": count}
            for (tool, payload), count in signatures.items()
            if tool and count > 2
        ]

    def _matching_indexes(self, names: list[str], pattern: str) -> list[int]:
        return [index for index, name in enumerate(names) if fnmatch.fnmatchcase(name, pattern)]


def format_completion_failure(report: dict[str, Any]) -> str:
    trajectory = report.get("trajectory") or {}
    details = {
        "missing_goals": report.get("missing_goals") or [],
        "missing_artifacts": report.get("missing_artifacts") or [],
        "integrity_violations": report.get("integrity_violations") or [],
        "database_integrity_violations": report.get("database_integrity_violations") or [],
        "missing_steps": trajectory.get("missing_steps") or [],
        "failed_steps": trajectory.get("failed_steps") or [],
        "unexpected_tools": trajectory.get("unexpected_tools") or [],
        "order_violations": trajectory.get("order_violations") or [],
        "argument_violations": trajectory.get("argument_violations") or [],
    }
    return f"Agent completion gate rejected the run: {json.dumps(details, ensure_ascii=False)}"
