from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMClient, llm_trace_context
from app.models.entities import AgentArtifact, AgentRun
from app.models.schemas import TaskState
from app.services.context_runtime import ContextIntegrityError, TokenEstimator


SUMMARY_FIELDS = (
    "discussion_summary",
    "rationales",
    "unresolved_questions",
    "source_message_ids",
    "task_state_version",
    "authoritative",
)
OPTIONAL_SUMMARY_FIELDS = ("task_state_claims", "historical_changes")


@dataclass(frozen=True)
class ConversationCompactionResult:
    recent_messages: list[dict[str, Any]]
    summary: dict[str, Any] | None
    summary_artifact_id: int | None
    compactor_called: bool
    compactor_attempts: int
    fallback_to_raw: bool
    validation_errors: list[str]
    original_tokens: int
    final_tokens: int


class ConversationCompactor:
    """Compress only old conversation; Profile/JD/Evidence remain authoritative."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.settings = get_settings()
        self.llm = llm or LLMClient()
        self.estimator = TokenEstimator()

    async def compact_if_needed(
        self,
        db: Session,
        *,
        run_id: int | None,
        messages: list[dict[str, Any]],
        node_budget_tokens: int,
        task_state: dict[str, Any] | TaskState | None = None,
    ) -> ConversationCompactionResult:
        normalized = self._normalize(messages)
        current_state = (
            task_state if isinstance(task_state, TaskState) else TaskState.model_validate(task_state or {})
        )
        recent_count = self.settings.conversation_recent_turns * 2
        older = normalized[:-recent_count] if len(normalized) > recent_count else []
        recent = normalized[-recent_count:]
        original_tokens = self.estimator.count(normalized).tokens
        threshold = int(node_budget_tokens * self.settings.conversation_compaction_budget_ratio)
        if not older or self.estimator.count(older).tokens <= threshold:
            return ConversationCompactionResult(
                recent_messages=normalized,
                summary=None,
                summary_artifact_id=None,
                compactor_called=False,
                compactor_attempts=0,
                fallback_to_raw=False,
                validation_errors=[],
                original_tokens=original_tokens,
                final_tokens=original_tokens,
            )
        if not self.llm.available:
            raise ContextIntegrityError("Conversation compaction requires an available LLM.")

        validation_errors: list[str] = []
        summary: dict[str, Any] | None = None
        for attempt in range(1, 3):
            raw_summary = await self._generate_summary(
                db,
                run_id=run_id,
                older=older,
                task_state=current_state,
                attempt=attempt,
                previous_error=validation_errors[-1] if validation_errors else None,
            )
            try:
                summary = self._validate_summary(raw_summary, older, task_state=current_state)
                break
            except ContextIntegrityError as exc:
                validation_errors.append(str(exc))
        if summary is None:
            if original_tokens <= node_budget_tokens:
                return ConversationCompactionResult(
                    recent_messages=normalized,
                    summary=None,
                    summary_artifact_id=None,
                    compactor_called=True,
                    compactor_attempts=2,
                    fallback_to_raw=True,
                    validation_errors=validation_errors,
                    original_tokens=original_tokens,
                    final_tokens=original_tokens,
                )
            raise ContextIntegrityError(
                "Conversation summary failed twice and raw messages exceed the planner budget: "
                + " | ".join(validation_errors)
            )
        artifact_id = self._persist_summary(db, run_id=run_id, summary=summary, older=older)
        final_tokens = self.estimator.count({"summary": summary, "recent_messages": recent}).tokens
        return ConversationCompactionResult(
            recent_messages=recent,
            summary=summary,
            summary_artifact_id=artifact_id,
            compactor_called=True,
            compactor_attempts=1 + int(bool(validation_errors)),
            fallback_to_raw=False,
            validation_errors=validation_errors,
            original_tokens=original_tokens,
            final_tokens=final_tokens,
        )

    async def _generate_summary(
        self,
        db: Session,
        *,
        run_id: int | None,
        older: list[dict[str, Any]],
        task_state: TaskState,
        attempt: int,
        previous_error: str | None,
    ) -> dict[str, Any]:
        system_prompt = (
            "你是 CareerAgent 对话压缩器。摘要只用于理解讨论背景，不是执行状态。只返回 JSON，"
            "字段为 discussion_summary、rationales、unresolved_questions、source_message_ids、"
            "task_state_version、authoritative、task_state_claims、historical_changes。"
            "authoritative 必须为 false；source_message_ids 必须覆盖所有输入消息；"
            "task_state_version 必须等于当前正式任务状态版本。task_state_claims 只能复述当前状态中的"
            "target_role、location、forbidden_actions、completed_actions，不得新增审批、禁止操作或完成结果。"
            "提到被纠正的旧值时，必须明确写成历史值或已失效值。"
        )
        user_payload = {
            "messages": older,
            "current_task_state": task_state.model_dump(),
            "previous_validation_error": previous_error,
        }
        batch_id = f"conversation-compaction:{run_id or 'detached'}:attempt-{attempt}"
        with llm_trace_context(
            run_id=run_id,
            graph_node="conversation_compactor",
            batch_id=batch_id,
            task_type="natural_language_planner",
            repair_type="summary_integrity" if attempt > 1 else None,
        ):
            return await self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=json.dumps(user_payload, ensure_ascii=False),
                temperature=0,
                max_tokens=self.settings.conversation_compactor_max_tokens,
                db=db,
                trace_name=(
                    "natural_language.conversation_compactor.repair"
                    if attempt > 1
                    else "natural_language.conversation_compactor"
                ),
                prompt_sections={
                    "conversation_history": older,
                    "working": {"task_state": task_state.model_dump()},
                    "repair_context": previous_error or "",
                },
            )

    def _validate_summary(
        self,
        summary: dict[str, Any],
        older: list[dict[str, Any]],
        *,
        task_state: TaskState,
    ) -> dict[str, Any]:
        if not isinstance(summary, dict):
            raise ContextIntegrityError("Conversation summary is not a JSON object.")
        missing_fields = [field for field in SUMMARY_FIELDS if field not in summary]
        if missing_fields:
            raise ContextIntegrityError(
                "Conversation summary is missing fields: " + ", ".join(missing_fields)
            )
        unknown_fields = sorted(set(summary) - set(SUMMARY_FIELDS) - set(OPTIONAL_SUMMARY_FIELDS))
        if unknown_fields:
            raise ContextIntegrityError(
                "Conversation summary contains unknown fields: " + ", ".join(unknown_fields)
            )
        if summary.get("authoritative") is not False:
            raise ContextIntegrityError("Conversation summary must be non-authoritative.")
        expected_ids = {str(item["message_id"]) for item in older}
        actual_ids = {str(item) for item in summary.get("source_message_ids") or []}
        if actual_ids != expected_ids:
            raise ContextIntegrityError("Conversation summary lost or invented source_message_ids.")
        if int(summary.get("task_state_version") or 0) != task_state.version:
            raise ContextIntegrityError("Conversation summary task_state_version does not match current state.")
        claims = summary.get("task_state_claims") or {}
        if not isinstance(claims, dict):
            raise ContextIntegrityError("Conversation summary task_state_claims must be an object.")
        unknown_claims = sorted(
            set(claims) - {"target_role", "location", "forbidden_actions", "completed_actions"}
        )
        if unknown_claims:
            raise ContextIntegrityError(
                "Conversation summary contains unsupported task state claims: "
                + ", ".join(unknown_claims)
            )
        for field in ("target_role", "location"):
            claimed = str(claims.get(field) or "").strip()
            if claimed and claimed != str(getattr(task_state, field) or ""):
                raise ContextIntegrityError(
                    f"Conversation summary conflicts with current task state field {field}."
                )
        for field in ("forbidden_actions", "completed_actions"):
            claimed_values = set(self._strings(claims.get(field)))
            actual_values = {str(item) for item in getattr(task_state, field)}
            invented = sorted(claimed_values - actual_values)
            if invented:
                raise ContextIntegrityError(
                    f"Conversation summary invented {field}: " + ", ".join(invented)
                )
        self._reject_active_old_values(summary, task_state)
        return {
            "discussion_summary": str(summary.get("discussion_summary") or ""),
            "rationales": self._strings(summary.get("rationales")),
            "unresolved_questions": self._strings(summary.get("unresolved_questions")),
            "source_message_ids": sorted(actual_ids),
            "task_state_version": task_state.version,
            "task_state_claims": {
                "target_role": str(claims.get("target_role") or ""),
                "location": str(claims.get("location") or ""),
                "forbidden_actions": self._strings(claims.get("forbidden_actions")),
                "completed_actions": self._strings(claims.get("completed_actions")),
            },
            "historical_changes": (
                summary.get("historical_changes")
                if isinstance(summary.get("historical_changes"), list)
                else []
            ),
            "authoritative": False,
        }

    @staticmethod
    def _reject_active_old_values(summary: dict[str, Any], task_state: TaskState) -> None:
        text = " ".join(
            [
                str(summary.get("discussion_summary") or ""),
                *[str(item) for item in summary.get("rationales") or []],
                *[str(item) for item in summary.get("unresolved_questions") or []],
            ]
        )
        historical_markers = ("曾", "原来", "原先", "此前", "历史", "已失效", "不再", "改为", "改到")
        for correction in task_state.corrections:
            old_value = correction.old_value.strip()
            if not old_value or old_value not in text:
                continue
            fragments = [fragment for fragment in re.split(r"[。！？；\n]", text) if old_value in fragment]
            if any(not any(marker in fragment for marker in historical_markers) for fragment in fragments):
                raise ContextIntegrityError(
                    f"Conversation summary presents corrected old value as active: {old_value}"
                )

    @staticmethod
    def _normalize(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output = []
        for index, raw in enumerate(messages):
            content = str(raw.get("content") or "").strip()
            if not content:
                continue
            output.append(
                {
                    "message_id": str(raw.get("message_id") or f"m{index + 1}"),
                    "role": str(raw.get("role") or "user"),
                    "content": content,
                    "critical_facts": [str(item) for item in raw.get("critical_facts") or []],
                }
            )
        return output

    @staticmethod
    def _strings(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    @staticmethod
    def _persist_summary(
        db: Session,
        *,
        run_id: int | None,
        summary: dict[str, Any],
        older: list[dict[str, Any]],
    ) -> int | None:
        if run_id is None:
            return None
        run = db.get(AgentRun, run_id)
        if run is None:
            raise ContextIntegrityError(f"Conversation summary run {run_id} does not exist.")
        source_hash = hashlib.sha256(
            json.dumps(older, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        artifact = AgentArtifact(
            run_id=run_id,
            artifact_type="conversation_summary",
            artifact_json={
                "version": "careeragent-conversation-summary-v2",
                "source_sha256": source_hash,
                "summary": summary,
            },
        )
        db.add(artifact)
        db.commit()
        db.refresh(artifact)
        return artifact.id
