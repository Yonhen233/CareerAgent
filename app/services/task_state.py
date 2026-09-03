from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from app.models.schemas import TaskState, TaskStateCorrection, TaskStateUpdates


class TaskStateValidationError(ValueError):
    pass


class TaskStateReducer:
    """Merge planner-produced deltas without giving the model full-state write access."""

    _FORBIDDEN_FALLBACKS: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "auto_apply",
            (
                r"不要自动投递|不自动投递|禁止自动投递|不要直接投递|先别投递",
                r"do not auto(?:matically)? apply|don't auto(?:matically)? apply",
            ),
        ),
        (
            "email_send",
            (r"不要发送邮件|不要发邮件|禁止发送邮件|先别发邮件", r"do not send (?:an )?email"),
        ),
        (
            "cross_tenant_data_access",
            (r"不要访问其他用户数据|禁止访问其他用户数据|不能读取其他用户数据",),
        ),
        (
            "external_send",
            (r"只生成草稿|仅生成草稿|只要草稿|不要外发", r"draft only|do not send externally"),
        ),
        (
            "unapproved_high_risk_action",
            (
                r"必须经过(?:我)?确认|需要我确认|确认后再(?:投递|发送|执行)|未经确认不要",
                r"(?:外发|投递|发送|执行)前(?:仍)?(?:需|要)确认",
            ),
        ),
    )

    _REMOVAL_MARKERS: dict[str, tuple[str, ...]] = {
        "auto_apply": ("可以自动投递", "允许自动投递", "解除自动投递限制"),
        "browser_apply": ("可以浏览器投递", "允许浏览器投递", "解除浏览器投递限制"),
        "email_send": ("可以发送邮件", "允许发送邮件", "解除邮件发送限制"),
        "cross_tenant_data_access": ("允许访问其他用户数据", "解除跨用户数据限制"),
        "external_send": ("可以外发", "允许外发", "不再只生成草稿"),
        "unapproved_high_risk_action": ("无需确认", "不用确认", "解除确认限制"),
    }

    @staticmethod
    def validate_updates(value: dict[str, Any] | TaskStateUpdates | None) -> TaskStateUpdates:
        try:
            return value if isinstance(value, TaskStateUpdates) else TaskStateUpdates.model_validate(value or {})
        except ValidationError as exc:
            raise TaskStateValidationError(f"Invalid task state updates: {exc}") from exc

    def merge(
        self,
        current: dict[str, Any] | TaskState | None,
        updates: dict[str, Any] | TaskStateUpdates | None,
        *,
        source_message_id: str,
        source_role: str,
        source_text: str,
    ) -> TaskState:
        if source_role != "user":
            raise TaskStateValidationError("Only the current user message may update task state.")
        if not source_message_id.strip():
            raise TaskStateValidationError("Task state updates require a source user message ID.")
        state = current if isinstance(current, TaskState) else TaskState.model_validate(current or {})
        delta = self.validate_updates(updates)
        candidate = state.model_copy(deep=True)
        changed = False

        for field in ("goal", "target_role", "location"):
            update = getattr(delta, field)
            if update is None:
                continue
            new_value = "" if update.operation == "clear" else str(update.value or "").strip()
            old_value = str(getattr(candidate, field) or "")
            if new_value == old_value:
                continue
            if old_value and new_value:
                candidate.corrections.append(
                    TaskStateCorrection(
                        field=field,
                        old_value=old_value,
                        new_value=new_value,
                        source_message_id=source_message_id,
                    )
                )
            setattr(candidate, field, new_value)
            candidate.provenance[field] = source_message_id
            changed = True

        changed |= self._merge_strings(
            candidate.constraints,
            additions=delta.constraints_to_add,
            removals=delta.constraints_to_remove,
            provenance=candidate.provenance,
            provenance_prefix="constraints",
            source_message_id=source_message_id,
        )

        for action in delta.forbidden_actions_to_remove:
            if action in candidate.forbidden_actions and not self._explicitly_allows(action, source_text):
                raise TaskStateValidationError(
                    f"Removing forbidden action {action} requires explicit permission in the current user message."
                )
        changed |= self._merge_strings(
            candidate.forbidden_actions,
            additions=delta.forbidden_actions_to_add,
            removals=delta.forbidden_actions_to_remove,
            provenance=candidate.provenance,
            provenance_prefix="forbidden_actions",
            source_message_id=source_message_id,
        )

        changed |= self._merge_strings(
            candidate.selected_actions,
            additions=delta.selected_actions_to_add,
            removals=delta.selected_actions_to_remove,
            provenance=candidate.provenance,
            provenance_prefix="selected_actions",
            source_message_id=source_message_id,
        )
        changed |= self._merge_strings(
            candidate.pending_actions,
            additions=delta.pending_actions_to_add,
            removals=delta.pending_actions_to_remove,
            provenance=candidate.provenance,
            provenance_prefix="pending_actions",
            source_message_id=source_message_id,
        )
        changed |= self._merge_strings(
            candidate.completed_actions,
            additions=delta.completed_actions_to_add,
            removals=(),
            provenance=candidate.provenance,
            provenance_prefix="completed_actions",
            source_message_id=source_message_id,
        )
        for action in delta.completed_actions_to_add:
            if action in candidate.pending_actions:
                candidate.pending_actions.remove(action)
                changed = True

        for action, patterns in self._FORBIDDEN_FALLBACKS:
            if any(re.search(pattern, source_text, flags=re.IGNORECASE) for pattern in patterns):
                if action not in candidate.forbidden_actions:
                    candidate.forbidden_actions.append(action)
                    changed = True
                    candidate.provenance[f"forbidden_actions.{action}"] = source_message_id

        candidate.constraints = list(dict.fromkeys(candidate.constraints))
        candidate.forbidden_actions = list(dict.fromkeys(candidate.forbidden_actions))
        candidate.selected_actions = list(dict.fromkeys(candidate.selected_actions))
        candidate.pending_actions = list(dict.fromkeys(candidate.pending_actions))
        candidate.completed_actions = list(dict.fromkeys(candidate.completed_actions))
        if changed:
            candidate.version = state.version + 1
        return TaskState.model_validate(candidate.model_dump())

    @staticmethod
    def _merge_strings(
        target: list,
        *,
        additions: list | tuple,
        removals: list | tuple,
        provenance: dict[str, str],
        provenance_prefix: str,
        source_message_id: str,
    ) -> bool:
        changed = False
        for item in removals:
            if item in target:
                target.remove(item)
                changed = True
        for item in additions:
            value = str(item).strip()
            if value and value not in target:
                target.append(item)
                changed = True
                provenance[f"{provenance_prefix}.{value}"] = source_message_id
        return changed

    def _explicitly_allows(self, action: str, source_text: str) -> bool:
        return any(marker in source_text for marker in self._REMOVAL_MARKERS.get(action, ()))
