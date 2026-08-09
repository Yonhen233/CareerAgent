from __future__ import annotations

import fnmatch
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import AgentApproval, AgentArtifact, AgentRun, AgentStep


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
            "version": "careeragent-task-contract-v1",
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
        passed = (
            not missing_goals
            and not missing_artifacts
            and not integrity_violations
            and trajectory["passed"]
        )
        return {
            "version": "careeragent-completion-gate-v1",
            "passed": passed,
            "terminal_decision": "completed" if passed else "failed_explicitly",
            "task_contract": contract,
            "goal_ledger": goal_ledger,
            "missing_goals": missing_goals,
            "missing_artifacts": missing_artifacts,
            "integrity_violations": integrity_violations,
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
    ) -> dict[str, Any]:
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
        return {
            "version": "careeragent-natural-completion-v1",
            "passed": not missing,
            "terminal_decision": "waiting_for_confirmation" if waiting and not missing else "completed" if not missing else "repair",
            "required_actions": sorted(actions),
            "action_checks": checks,
            "missing_actions": missing,
        }

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
        "missing_steps": trajectory.get("missing_steps") or [],
        "failed_steps": trajectory.get("failed_steps") or [],
        "unexpected_tools": trajectory.get("unexpected_tools") or [],
        "order_violations": trajectory.get("order_violations") or [],
        "argument_violations": trajectory.get("argument_violations") or [],
    }
    return f"Agent completion gate rejected the run: {json.dumps(details, ensure_ascii=False)}"
