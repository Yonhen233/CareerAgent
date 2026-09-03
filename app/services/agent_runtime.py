from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar
from uuid import uuid4

import httpx
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.agents.skills import TASK_SKILL_MAPPING, get_skill_registry
from app.agents.tools import AgentToolSpec, BoundAgentTool, get_agent_tool
from app.core.config import Settings, get_settings
from app.core.llm import LLMBudgetExceededError, LLMConfigurationError, LLMResponseError
from app.core.redis_client import RedisUnavailableError
from app.models.entities import AgentApproval, AgentRun, Job, Profile, ResumeVersion, ToolCircuitState
from app.services.agent_reliability import (
    AgentExecutionBudgetExceeded,
    AgentTaskIncompleteError,
)
from app.services.retrieval_quality import RetrievalQualityError
from app.core.redaction import SecurityRedactor

T = TypeVar("T")
EventSink = Callable[[str, dict[str, Any]], None]


class AgentToolContractError(RuntimeError):
    pass


class AgentToolPolicyError(RuntimeError):
    pass


class AgentToolTimeoutError(TimeoutError):
    pass


class AgentToolCircuitOpenError(RuntimeError):
    pass


@dataclass(frozen=True)
class ErrorEnvelope:
    error_id: str
    category: str
    code: str
    message: str
    retryable: bool
    recovery_action: str
    origin: dict[str, Any]
    occurred_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentErrorClassifier:
    """Convert arbitrary exceptions into a stable recovery taxonomy."""

    def __init__(self, *, redactor: SecurityRedactor | None = None) -> None:
        self.redactor = redactor or SecurityRedactor()

    def classify(
        self,
        exc: Exception,
        *,
        tool_name: str | None = None,
        step_name: str | None = None,
        attempt: int | None = None,
    ) -> ErrorEnvelope:
        category, retryable, action = self._policy(exc)
        message = str(self.redactor.redact(str(exc)))[:2000]
        return ErrorEnvelope(
            error_id=uuid4().hex,
            category=category,
            code=exc.__class__.__name__,
            message=message,
            retryable=retryable,
            recovery_action=action,
            origin={
                "tool_name": tool_name,
                "step_name": step_name,
                "attempt": attempt,
            },
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _policy(exc: Exception) -> tuple[str, bool, str]:
        name = exc.__class__.__name__
        if isinstance(exc, AgentToolCircuitOpenError):
            return "dependency_circuit_open", False, "wait_for_cooldown_or_manual_probe"
        if isinstance(exc, (AgentToolTimeoutError, asyncio.TimeoutError, httpx.TimeoutException)):
            return "dependency_timeout", True, "bounded_retry_then_dlq"
        if isinstance(
            exc,
            (httpx.TransportError, ConnectionError, RedisUnavailableError, OperationalError, sqlite3.OperationalError),
        ):
            return "dependency_transient", True, "bounded_retry_then_dlq"
        if isinstance(exc, RetrievalQualityError):
            return "insufficient_evidence", False, "request_better_evidence_or_change_query"
        if isinstance(exc, LLMConfigurationError):
            return "configuration_error", False, "configure_provider_before_retry"
        if isinstance(exc, LLMBudgetExceededError):
            return "model_budget_exceeded", False, "reduce_context_or_raise_explicit_budget"
        if isinstance(exc, LLMResponseError):
            return "model_response_invalid", False, "schema_repair_or_model_review"
        if isinstance(exc, AgentExecutionBudgetExceeded):
            return "execution_budget_exceeded", False, "inspect_loop_and_replan"
        if isinstance(exc, AgentTaskIncompleteError):
            return "completion_gate_rejected", False, "repair_missing_goals"
        if name == "OutboundToolError":
            if "required" in str(exc).lower() or "install" in str(exc).lower():
                return "configuration_error", False, "configure_outbound_dependency_before_retry"
            return "dependency_transient", True, "manual_review_before_retrying_side_effect"
        if name == "InterviewAgenticRAGError":
            return "insufficient_or_invalid_evidence", False, "inspect_retrieval_and_claim_trace"
        if name in {"ApprovalRequiredError", "RunWithdrawalConflict", "AgentRunCancelled"}:
            return "policy_or_human_interrupt", False, "wait_for_human_or_stop"
        if isinstance(exc, AgentToolPolicyError):
            return "tool_policy_rejected", False, "request_authorized_capability_or_valid_approval"
        if isinstance(exc, AgentToolContractError):
            return "tool_contract_violation", False, "fix_tool_arguments_or_output_contract"
        if isinstance(exc, (ValueError, FileNotFoundError)):
            return "input_or_state_validation", False, "correct_input_or_state"
        if isinstance(exc, (AssertionError, KeyError, TypeError, AttributeError)):
            return "internal_invariant_violation", False, "open_quality_review_and_fix_code"
        return "internal_error", False, "open_quality_review_and_inspect_trace"


class AgentToolRuntime:
    """Enforce tool contracts and one-owner retry/circuit policies at runtime."""

    RETRYABLE_ALIASES = {
        "dependency_timeout": "timeout",
        "dependency_transient": "connection_error",
        "model_response_invalid": "invalid_json",
    }
    CONTROL_PLANE_TOOLS = {
        "LangGraph.AgentPlanner",
        "llm.intent_planner",
        "NaturalLanguageAgentService",
    }

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.classifier = AgentErrorClassifier()

    async def execute(
        self,
        db: Session,
        *,
        run_id: int,
        step_name: str,
        input_json: dict[str, Any] | None,
        tool: BoundAgentTool[T],
        event_sink: EventSink,
    ) -> T:
        contract = self._validated_binding(tool)
        if contract.execution_mode != "async":
            raise AgentToolContractError(
                f"Tool {contract.name} requires the {contract.execution_mode} runtime executor."
            )
        tool_name = contract.name
        self._validate_input(contract, input_json or {})
        self._assert_runtime_policy(
            db,
            run_id=run_id,
            contract=contract,
            payload=input_json or {},
            event_sink=event_sink,
        )
        self._assert_circuit_allows(db, contract, event_sink=event_sink)
        max_attempts = self._runtime_attempts(contract)

        for attempt in range(1, max_attempts + 1):
            try:
                output = await asyncio.wait_for(tool.handler(), timeout=contract.timeout_seconds)
                self._validate_output(contract, output)
                self._record_success(db, contract)
                if attempt > 1:
                    event_sink(
                        "tool_retry_recovered",
                        {"tool_name": tool_name, "attempt": attempt, "max_attempts": max_attempts},
                    )
                return output
            except Exception as exc:
                db.rollback()
                envelope = self.classifier.classify(
                    exc,
                    tool_name=tool_name,
                    step_name=step_name,
                    attempt=attempt,
                )
                self._record_failure(db, contract, envelope, event_sink=event_sink)
                retry = attempt < max_attempts and self._can_retry(contract, envelope)
                event_sink(
                    "tool_attempt_failed",
                    {
                        "tool_name": tool_name,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "will_retry": retry,
                        "error_envelope": envelope.as_dict(),
                    },
                )
                if not retry:
                    raise
                delay = self.settings.agent_tool_retry_backoff_seconds * attempt
                event_sink(
                    "tool_retry_scheduled",
                    {
                        "tool_name": tool_name,
                        "next_attempt": attempt + 1,
                        "delay_seconds": delay,
                        "error_id": envelope.error_id,
                    },
                )
                if delay > 0:
                    await asyncio.sleep(delay)

        raise RuntimeError(f"Tool {tool_name} exhausted without a terminal result.")

    def execute_sync(
        self,
        db: Session,
        *,
        run_id: int,
        step_name: str,
        input_json: dict[str, Any],
        tool: BoundAgentTool[T],
        event_sink: EventSink,
    ) -> T:
        """Guard sync outbound tools; their clients own transport-level timeouts."""
        contract = self._validated_binding(tool)
        if contract.execution_mode != "sync":
            raise AgentToolContractError(
                f"Tool {contract.name} requires the {contract.execution_mode} runtime executor."
            )
        tool_name = contract.name
        self._validate_input(contract, input_json)
        self._assert_runtime_policy(
            db,
            run_id=run_id,
            contract=contract,
            payload=input_json,
            event_sink=event_sink,
        )
        self._assert_circuit_allows(db, contract, event_sink=event_sink)
        try:
            output = tool.handler()
            self._validate_output(contract, output)
            self._record_success(db, contract)
            return output
        except Exception as exc:
            db.rollback()
            envelope = self.classifier.classify(
                exc,
                tool_name=tool_name,
                step_name=step_name,
                attempt=1,
            )
            self._record_failure(db, contract, envelope, event_sink=event_sink)
            event_sink(
                "tool_attempt_failed",
                {
                    "tool_name": tool_name,
                    "attempt": 1,
                    "max_attempts": 1,
                    "will_retry": False,
                    "error_envelope": envelope.as_dict(),
                    "timeout_owner": "outbound_client",
                },
            )
            raise

    def _validated_binding(self, tool: BoundAgentTool[T]) -> AgentToolSpec:
        registered = self._resolve_contract(tool.spec.name)
        if tool.spec != registered:
            raise AgentToolContractError(
                f"Bound tool {tool.spec.name} does not match the registered immutable contract."
            )
        return registered

    def _assert_runtime_policy(
        self,
        db: Session,
        *,
        run_id: int,
        contract: AgentToolSpec,
        payload: dict[str, Any],
        event_sink: EventSink,
    ) -> None:
        run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
        if run is None:
            raise AgentToolPolicyError(f"Agent run {run_id} was not found for tool policy evaluation.")
        if run.status in {"cancelled", "withdrawn"}:
            raise AgentToolPolicyError(f"Agent run {run_id} is {run.status}; tool execution is forbidden.")

        self._assert_resource_scope(db, run=run, payload=payload)

        allowed_tools = set(self.CONTROL_PLANE_TOOLS)
        if run.task_type in TASK_SKILL_MAPPING:
            allowed_tools.update(get_skill_registry().allowed_tools_for_task(run.task_type))
        elif run.task_type == "natural_language_request":
            allowed_tools.update(self.CONTROL_PLANE_TOOLS)
        else:
            # Unknown task types do not receive an implicit business capability set.
            allowed_tools.update(self.CONTROL_PLANE_TOOLS)
        if contract.name not in allowed_tools:
            raise AgentToolPolicyError(
                f"Tool {contract.name} is not authorized for task type {run.task_type}."
            )

        approval_id: int | None = None
        if contract.approval_requirement != "none":
            raw_approval_id = payload.get("approval_id")
            if not isinstance(raw_approval_id, int) or raw_approval_id <= 0:
                raise AgentToolPolicyError(
                    f"Tool {contract.name} requires an approved {contract.approval_requirement} approval_id."
                )
            approval = (
                db.query(AgentApproval)
                .filter(
                    AgentApproval.id == raw_approval_id,
                    AgentApproval.run_id == run_id,
                    AgentApproval.action_type == contract.approval_requirement,
                )
                .first()
            )
            allowed_approval_statuses = {"approved"}
            if contract.name in {"browser_apply", "email_draft", "email_send"}:
                allowed_approval_statuses.add("executing")
            if approval is None or approval.status not in allowed_approval_statuses:
                raise AgentToolPolicyError(
                    f"Approval {raw_approval_id} is missing, not approved, or not bound to run {run_id} "
                    f"and action {contract.approval_requirement}."
                )
            summary = approval.payload_summary_json or {}
            mismatched = [
                key for key, value in summary.items() if key in payload and payload[key] != value
            ]
            if mismatched:
                raise AgentToolPolicyError(
                    f"Approval {raw_approval_id} payload does not match execution fields: "
                    f"{', '.join(sorted(mismatched))}."
                )
            approval_id = approval.id

        event_sink(
            "tool_policy_checked",
            {
                "tool_name": contract.name,
                "task_type": run.task_type,
                "capability_authorized": True,
                "approval_requirement": contract.approval_requirement,
                "approval_id": approval_id,
                "binding_version": "careeragent-bound-tool-v1",
                "contract_version": contract.contract_version,
            },
        )

    def _resolve_contract(self, tool_name: str) -> AgentToolSpec:
        try:
            return get_agent_tool(tool_name)
        except KeyError as exc:
            if self.settings.agent_strict_tool_contracts:
                raise AgentToolContractError(f"No runtime contract is registered for tool {tool_name}.") from exc
            return AgentToolSpec(
                name=tool_name,
                purpose="unregistered compatibility tool",
                input_schema={},
                output_schema={},
                side_effects=[],
            )

    @staticmethod
    def _validate_input(contract: AgentToolSpec, payload: dict[str, Any]) -> None:
        missing: list[str] = []
        invalid: list[str] = []
        for field_name, type_name in contract.input_schema.items():
            alternatives = [item.strip() for item in field_name.split("|") if item.strip()]
            optional = "None" in type_name or "optional" in type_name.lower()
            present = [name for name in alternatives if payload.get(name) is not None]
            if not optional and alternatives and not present:
                missing.append(field_name)
                continue
            for name in present:
                value = payload[name]
                if not AgentToolRuntime._matches_type(value, type_name):
                    invalid.append(f"{name} expected {type_name}, got {type(value).__name__}")
                    continue
                if (name == "id" or name.endswith("_id") or name in {"limit", "top_k"}) and isinstance(value, int):
                    if value <= 0:
                        invalid.append(f"{name} must be greater than zero")
        if missing:
            raise AgentToolContractError(
                f"Tool {contract.name} is missing required input fields: {', '.join(missing)}."
            )
        if invalid:
            raise AgentToolContractError(
                f"Tool {contract.name} has invalid input fields: {'; '.join(invalid)}."
            )
        if not contract.allow_extra_input:
            allowed = {
                alternative
                for field_name in contract.input_schema
                for alternative in field_name.split("|")
                if alternative
            }
            unexpected = sorted(set(payload) - allowed)
            if unexpected:
                raise AgentToolContractError(
                    f"Tool {contract.name} received unexpected input fields: {', '.join(unexpected)}."
                )

    @staticmethod
    def _assert_resource_scope(db: Session, *, run: AgentRun, payload: dict[str, Any]) -> None:
        """Repeat tenant checks inside the worker boundary, not only at the HTTP edge."""
        if not run.tenant_id:
            return

        profile_id = payload.get("profile_id")
        if isinstance(profile_id, int):
            profile = db.query(Profile).filter(Profile.id == profile_id).first()
            if profile is None or profile.tenant_id != run.tenant_id:
                raise AgentToolPolicyError(
                    f"Profile {profile_id} is not accessible to tenant {run.tenant_id}."
                )

        job_id = payload.get("job_id")
        if isinstance(job_id, int):
            job = db.query(Job).filter(Job.id == job_id).first()
            if job is None or job.tenant_id not in {None, run.tenant_id}:
                raise AgentToolPolicyError(
                    f"Job {job_id} is not accessible to tenant {run.tenant_id}."
                )

        resume_version_id = payload.get("resume_version_id")
        if isinstance(resume_version_id, int):
            version = db.query(ResumeVersion).filter(ResumeVersion.id == resume_version_id).first()
            profile = (
                db.query(Profile).filter(Profile.id == version.profile_id).first()
                if version is not None
                else None
            )
            if version is None or profile is None or profile.tenant_id != run.tenant_id:
                raise AgentToolPolicyError(
                    f"ResumeVersion {resume_version_id} is not accessible to tenant {run.tenant_id}."
                )

    @staticmethod
    def _validate_output(contract: AgentToolSpec, output: Any) -> None:
        if output is None and contract.output_schema:
            raise AgentToolContractError(f"Tool {contract.name} returned None for a non-empty output contract.")
        if contract.name in {"LangGraph.AgentPlanner", "llm.intent_planner", "NaturalLanguageAgentService"}:
            if not isinstance(output, dict):
                raise AgentToolContractError(f"Tool {contract.name} must return a dictionary.")
        if contract.name == "LangGraph.AgentPlanner":
            if not isinstance(output.get("task_type"), str) or not isinstance(output.get("steps"), list):
                raise AgentToolContractError(
                    "LangGraph.AgentPlanner output must include task_type and executable steps."
                )
        if contract.name == "llm.intent_planner":
            if not isinstance(output.get("intent"), str) or not isinstance(output.get("actions"), list):
                raise AgentToolContractError(
                    "llm.intent_planner output must include normalized intent and actions."
                )
        if contract.name == "job_search.search_jobs":
            if (
                not isinstance(output, tuple)
                or len(output) != 2
                or not isinstance(output[0], list)
                or not isinstance(output[1], dict)
            ):
                raise AgentToolContractError("job_search.search_jobs must return (jobs, source_errors).")
        if contract.name == "matcher.match_job" and not hasattr(output, "overall_score") and not isinstance(output, dict):
            raise AgentToolContractError("matcher.match_job returned an invalid match result.")
        if contract.name == "matcher.enforce_fit_gate":
            if not isinstance(output, dict):
                raise AgentToolContractError("matcher.enforce_fit_gate must return a dictionary.")
            missing = [field for field in contract.output_schema if output.get(field) is None]
            invalid = [
                f"{field} expected {type_name}, got {type(output[field]).__name__}"
                for field, type_name in contract.output_schema.items()
                if field in output and not AgentToolRuntime._matches_type(output[field], type_name)
            ]
            if missing or invalid:
                details = [*(f"missing {field}" for field in missing), *invalid]
                raise AgentToolContractError(
                    f"Tool {contract.name} returned an invalid fit-gate result: {'; '.join(details)}."
                )
        entity_contracts = {
            "profile_repository.load_profile": "Profile",
            "job_repository.load_job": "Job",
            "resume_tailor.tailor_resume": "ResumeVersion",
            "application.create_quick_apply_packet": "Application",
            "interview_prep.generate_packet": "InterviewPrep",
            "interview_experience.import_text": "InterviewExperience",
        }
        expected_entity = entity_contracts.get(contract.name)
        if expected_entity and output.__class__.__name__ != expected_entity:
            raise AgentToolContractError(
                f"Tool {contract.name} expected {expected_entity}, got {output.__class__.__name__}."
            )
        if contract.name == "vector_index.upsert_job_chunks" and not AgentToolRuntime._matches_type(output, "int"):
            raise AgentToolContractError("vector_index.upsert_job_chunks must return an integer.")
        if contract.name in {
            "vector_index.retrieve_resume_evidence",
            "guardrail.verify_resume",
            "browser_apply",
            "email_draft",
            "email_send",
        }:
            if not isinstance(output, dict):
                raise AgentToolContractError(f"Tool {contract.name} must return a dictionary.")
            missing = [field for field in contract.output_schema if output.get(field) is None]
            if missing:
                raise AgentToolContractError(
                    f"Tool {contract.name} is missing output fields: {', '.join(missing)}."
                )
            invalid = [
                f"{field} expected {type_name}, got {type(output[field]).__name__}"
                for field, type_name in contract.output_schema.items()
                if not AgentToolRuntime._matches_type(output[field], type_name)
            ]
            if invalid:
                raise AgentToolContractError(
                    f"Tool {contract.name} has invalid output fields: {'; '.join(invalid)}."
                )

    @staticmethod
    def _matches_type(value: Any, type_name: str) -> bool:
        expression = str(type_name or "Any").strip()
        options = [item.strip() for item in expression.split("|") if item.strip()]
        if "None" in options and value is None:
            return True
        options = [item for item in options if item not in {"None", "optional"}]
        if len(options) > 1 and all(
            item not in {"str", "int", "float", "bool", "dict", "list", "datetime", "Any"}
            and not item.startswith("list[")
            for item in options
        ):
            return isinstance(value, str) and value in options
        if len(options) > 1:
            return any(AgentToolRuntime._matches_type(value, item) for item in options)
        option = options[0] if options else "Any"
        if option == "Any":
            return True
        if option == "str":
            return isinstance(value, str)
        if option == "int":
            return isinstance(value, int) and not isinstance(value, bool)
        if option == "float":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if option == "bool":
            return isinstance(value, bool)
        if option == "dict":
            return isinstance(value, dict)
        if option == "list":
            return isinstance(value, list)
        if option == "datetime":
            return isinstance(value, (datetime, str))
        if option.startswith("list["):
            return isinstance(value, list)
        if option and option[0].islower():
            return isinstance(value, str) and value == option
        return value.__class__.__name__ == option

    @staticmethod
    def _scope_key(contract: AgentToolSpec) -> str:
        return "external" if any("external" in item or "llm" in item for item in contract.side_effects) else "local"

    def _assert_circuit_allows(
        self,
        db: Session,
        contract: AgentToolSpec,
        *,
        event_sink: EventSink,
    ) -> None:
        state = self._get_circuit(db, contract)
        if state is None or state.status == "closed":
            return
        now = datetime.now(timezone.utc)
        open_until = self._aware(state.open_until)
        if open_until and open_until <= now:
            state.status = "half_open"
            db.add(state)
            db.commit()
            event_sink("tool_circuit_half_open", {"tool_name": contract.name, "scope_key": state.scope_key})
            return
        raise AgentToolCircuitOpenError(
            f"Tool circuit is open for {contract.name} until {open_until.isoformat() if open_until else 'manual reset'}."
        )

    def _record_failure(
        self,
        db: Session,
        contract: AgentToolSpec,
        envelope: ErrorEnvelope,
        *,
        event_sink: EventSink,
    ) -> None:
        if not envelope.retryable:
            return
        state = self._get_circuit(db, contract)
        if state is None:
            state = ToolCircuitState(
                tool_name=contract.name,
                scope_key=self._scope_key(contract),
                status="closed",
                consecutive_failures=0,
            )
        state.consecutive_failures = int(state.consecutive_failures or 0) + 1
        state.last_error_category = envelope.category
        if state.consecutive_failures >= self.settings.agent_tool_circuit_failure_threshold:
            now = datetime.now(timezone.utc)
            state.status = "open"
            state.opened_at = now
            state.open_until = now + timedelta(seconds=self.settings.agent_tool_circuit_cooldown_seconds)
            event_sink(
                "tool_circuit_opened",
                {
                    "tool_name": contract.name,
                    "failure_count": state.consecutive_failures,
                    "open_until": state.open_until.isoformat(),
                    "error_id": envelope.error_id,
                },
            )
        db.add(state)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            concurrent = self._get_circuit(db, contract)
            if concurrent is None:
                raise
            concurrent.consecutive_failures = int(concurrent.consecutive_failures or 0) + 1
            concurrent.last_error_category = envelope.category
            if concurrent.consecutive_failures >= self.settings.agent_tool_circuit_failure_threshold:
                now = datetime.now(timezone.utc)
                concurrent.status = "open"
                concurrent.opened_at = now
                concurrent.open_until = now + timedelta(
                    seconds=self.settings.agent_tool_circuit_cooldown_seconds
                )
                event_sink(
                    "tool_circuit_opened",
                    {
                        "tool_name": contract.name,
                        "failure_count": concurrent.consecutive_failures,
                        "open_until": concurrent.open_until.isoformat(),
                        "error_id": envelope.error_id,
                        "concurrent_update": True,
                    },
                )
            db.add(concurrent)
            db.commit()

    def _record_success(self, db: Session, contract: AgentToolSpec) -> None:
        state = self._get_circuit(db, contract)
        if state is None or (state.status == "closed" and state.consecutive_failures == 0):
            return
        state.status = "closed"
        state.consecutive_failures = 0
        state.last_error_category = None
        state.opened_at = None
        state.open_until = None
        db.add(state)
        db.commit()

    def _get_circuit(self, db: Session, contract: AgentToolSpec) -> ToolCircuitState | None:
        return (
            db.query(ToolCircuitState)
            .filter(
                ToolCircuitState.tool_name == contract.name,
                ToolCircuitState.scope_key == self._scope_key(contract),
            )
            .first()
        )

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _runtime_attempts(contract: AgentToolSpec) -> int:
        if contract.retry_owner != "runtime":
            return 1
        return max(1, int(contract.retry_policy.get("max_attempts") or 1))

    def _can_retry(self, contract: AgentToolSpec, envelope: ErrorEnvelope) -> bool:
        if contract.retry_owner != "runtime" or not envelope.retryable:
            return False
        allowed = {str(item) for item in contract.retry_policy.get("retryable_errors") or []}
        return envelope.category in allowed or self.RETRYABLE_ALIASES.get(envelope.category) in allowed
