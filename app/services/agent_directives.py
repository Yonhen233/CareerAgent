from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agents.natural_language import NaturalLanguageAgentService
from app.models.entities import AgentDirective, AgentRun
from app.models.schemas import AgentDirectiveRequest, NaturalLanguageAgentRequest
from app.services.trace_service import TraceService


ACTIVE_RUN_STATUSES = {"queued", "running", "waiting_for_confirmation"}


class AgentDirectiveService:
    def __init__(
        self,
        *,
        natural_language: NaturalLanguageAgentService | None = None,
        trace: TraceService | None = None,
    ) -> None:
        self.natural_language = natural_language or NaturalLanguageAgentService()
        self.trace = trace or TraceService()

    async def append(
        self,
        db: Session,
        *,
        source_run: AgentRun,
        payload: AgentDirectiveRequest,
    ) -> AgentDirective:
        if source_run.status in ACTIVE_RUN_STATUSES:
            raise ValueError(
                "The source run is still active. Wait for completion or cancel it before creating a follow-up branch."
            )
        if source_run.status == "withdrawn":
            raise ValueError("A withdrawn run cannot be used as the source of a follow-up branch.")
        instruction = payload.instruction.strip()
        selected_actions = list(dict.fromkeys(payload.selected_actions))
        idempotency_key = payload.client_request_id or f"directive-{uuid4().hex}"
        existing = (
            db.query(AgentDirective)
            .filter(
                AgentDirective.source_run_id == source_run.id,
                AgentDirective.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing is not None:
            if existing.instruction != instruction or existing.selected_actions_json != selected_actions:
                raise ValueError("The idempotency key was already used with a different follow-up payload.")
            return existing

        context = self._compact_source_context(source_run)
        directive = AgentDirective(
            source_run_id=source_run.id,
            tenant_id=source_run.tenant_id,
            user_id=source_run.user_id,
            instruction=instruction,
            selected_actions_json=selected_actions,
            context_json=context,
            idempotency_key=idempotency_key,
            status="executing",
        )
        db.add(directive)
        db.commit()
        db.refresh(directive)
        self.trace.add_event(
            db,
            run_id=source_run.id,
            event_type="user_directive_received",
            node_name="follow_up_branch",
            payload={
                "directive_id": directive.id,
                "mode": directive.mode,
                "instruction_preview": directive.instruction[:240],
            },
        )

        request = NaturalLanguageAgentRequest(
            instruction=self._follow_up_instruction(directive.instruction),
            profile_id=self._profile_id(source_run),
            job_id=self._job_id(source_run),
            resume_version_id=self._resume_version_id(source_run),
            profile_context={"follow_up_source": context},
            selected_actions=directive.selected_actions_json,
            query=self._value(source_run.input_json, "query") or "Agent 开发实习生",
            location=self._value(source_run.input_json, "location"),
            limit=self._bounded_limit(self._value(source_run.input_json, "limit")),
        )
        try:
            target_run = await self.natural_language.run(
                db,
                request,
                tenant_id=source_run.tenant_id,
                user_id=source_run.user_id,
            )
            target_input = dict(target_run.input_json or {})
            target_input.update(
                {
                    "parent_run_id": source_run.id,
                    "directive_id": directive.id,
                    "conversation_root_run_id": self._conversation_root(source_run),
                    "contract_revision": 1,
                }
            )
            target_run.input_json = target_input
            directive.target_run_id = target_run.id
            directive.status = "completed" if target_run.status != "failed" else "failed"
            directive.error_message = target_run.error_message
            directive.result_json = {
                "target_run_status": target_run.status,
                "target_run_id": target_run.id,
                "user_message": (target_run.output_json or {}).get("user_message"),
            }
            directive.completed_at = datetime.now(timezone.utc)
            db.add_all([target_run, directive])
            db.commit()
            db.refresh(directive)
            self.trace.add_artifact(
                db,
                run_id=target_run.id,
                artifact_type="task_contract_revision",
                payload={
                    "version": "careeragent-follow-up-contract-v1",
                    "revision": 1,
                    "parent_run_id": source_run.id,
                    "directive_id": directive.id,
                    "new_instruction": directive.instruction,
                    "rule": "The parent run remains immutable; only the explicit follow-up instruction is new work.",
                },
            )
            self.trace.add_event(
                db,
                run_id=source_run.id,
                event_type="user_directive_completed" if directive.status == "completed" else "user_directive_failed",
                node_name="follow_up_branch",
                payload={
                    "directive_id": directive.id,
                    "target_run_id": target_run.id,
                    "target_run_status": target_run.status,
                },
            )
            return directive
        except Exception as exc:
            directive.status = "failed"
            directive.error_message = str(exc)
            directive.completed_at = datetime.now(timezone.utc)
            db.add(directive)
            db.commit()
            db.refresh(directive)
            self.trace.add_event(
                db,
                run_id=source_run.id,
                event_type="user_directive_failed",
                node_name="follow_up_branch",
                payload={"directive_id": directive.id, "error": str(exc)},
            )
            raise

    def list_for_run(self, db: Session, *, source_run_id: int, limit: int = 50) -> list[AgentDirective]:
        return (
            db.query(AgentDirective)
            .filter(AgentDirective.source_run_id == source_run_id)
            .order_by(AgentDirective.created_at.desc(), AgentDirective.id.desc())
            .limit(limit)
            .all()
        )

    def _compact_source_context(self, run: AgentRun) -> dict[str, Any]:
        input_json = self._mapping(run.input_json)
        output = self._mapping(run.output_json)
        result = output.get("result_json") if isinstance(output.get("result_json"), dict) else {}
        selected_job = output.get("selected_job") or result.get("selected_job") or {}
        selected_job = self._mapping(selected_job)
        application = self._mapping(output.get("application"))
        interview_prep = self._mapping(output.get("interview_prep"))
        return {
            "source_run_id": run.id,
            "source_task_type": run.task_type,
            "source_status": run.status,
            "profile_id": self._profile_id(run),
            "job_id": self._job_id(run),
            "resume_version_id": self._resume_version_id(run),
            "original_instruction": self._truncate(input_json.get("instruction"), 800),
            "original_query": self._truncate(input_json.get("query"), 400),
            "location": input_json.get("location"),
            "selected_job": {
                key: selected_job.get(key)
                for key in ("job_id", "title", "company", "location")
                if selected_job.get(key) is not None
            },
            "prior_user_message": self._truncate(output.get("user_message"), 600),
            "available_artifact_ids": {
                "resume_version_id": self._resume_version_id(run),
                "application_id": output.get("application_id") or application.get("application_id"),
                "interview_prep_id": output.get("interview_prep_id") or interview_prep.get("interview_prep_id"),
            },
        }

    @staticmethod
    def _follow_up_instruction(instruction: str) -> str:
        return (
            "这是对上一轮求职流程的追加指令。只处理用户新增或修正的要求；"
            "复用上下文中的 profile_id、job_id 和已有材料，不要重复用户没有要求重做的动作。"
            "如果新增要求会产生投递、邮件发送或浏览器提交，仍必须经过原有审批。\n\n"
            f"用户追加指令：{instruction}"
        )

    @staticmethod
    def _resume_version_id(run: AgentRun) -> int | None:
        input_json = AgentDirectiveService._mapping(run.input_json)
        output = AgentDirectiveService._mapping(run.output_json)
        result = output.get("result_json") if isinstance(output.get("result_json"), dict) else {}
        tailor = AgentDirectiveService._mapping(output.get("tailor"))
        result_tailor = AgentDirectiveService._mapping(result.get("tailor"))
        value = (
            input_json.get("resume_version_id")
            or output.get("resume_version_id")
            or tailor.get("resume_version_id")
            or result_tailor.get("resume_version_id")
        )
        return AgentDirectiveService._safe_int(value)

    @staticmethod
    def _profile_id(run: AgentRun) -> int | None:
        output = AgentDirectiveService._mapping(run.output_json)
        result = output.get("result_json") if isinstance(output.get("result_json"), dict) else {}
        value = run.profile_id or AgentDirectiveService._mapping(result.get("profile")).get("id")
        return AgentDirectiveService._safe_int(value)

    @staticmethod
    def _job_id(run: AgentRun) -> int | None:
        output = AgentDirectiveService._mapping(run.output_json)
        result = output.get("result_json") if isinstance(output.get("result_json"), dict) else {}
        value = run.job_id or AgentDirectiveService._mapping(result.get("job")).get("id")
        return AgentDirectiveService._safe_int(value)

    @staticmethod
    def _conversation_root(run: AgentRun) -> int:
        value = AgentDirectiveService._mapping(run.input_json).get("conversation_root_run_id")
        return AgentDirectiveService._safe_int(value) or run.id

    @staticmethod
    def _value(payload: dict[str, Any] | None, key: str) -> Any:
        return AgentDirectiveService._mapping(payload).get(key)

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bounded_limit(value: Any) -> int:
        parsed = AgentDirectiveService._safe_int(value)
        return max(1, min(parsed if parsed is not None else 8, 30))

    @staticmethod
    def _truncate(value: Any, maximum: int) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text if len(text) <= maximum else text[:maximum] + "..."
