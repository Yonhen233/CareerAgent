from __future__ import annotations

from typing import Any

from app.services.context_runtime import ContextBudgetExceededError, TokenEstimator


class SharedContextBatcher:
    """Split independent items without duplicating shared Profile/JD/Evidence per item."""

    def __init__(self, estimator: TokenEstimator | None = None) -> None:
        self.estimator = estimator or TokenEstimator()

    def split(
        self,
        items: list[dict[str, Any]],
        *,
        shared_context: dict[str, Any],
        max_items: int,
        max_input_tokens: int,
    ) -> list[list[dict[str, Any]]]:
        if not items:
            return []
        shared_tokens = self.estimator.count(shared_context).tokens
        if shared_tokens >= max_input_tokens:
            raise ContextBudgetExceededError("Shared batch context alone exceeds the node budget.")
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_tokens = shared_tokens
        for item in items:
            item_tokens = self.estimator.count(item).tokens
            if item_tokens + shared_tokens > max_input_tokens:
                raise ContextBudgetExceededError("A single batch item exceeds the node budget.")
            if current and (
                len(current) >= max(max_items, 1)
                or current_tokens + item_tokens > max_input_tokens
            ):
                batches.append(current)
                current = []
                current_tokens = shared_tokens
            current.append(item)
            current_tokens += item_tokens
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def packet(
        *, shared_context: dict[str, Any], items: list[dict[str, Any]], batch_id: str
    ) -> dict[str, Any]:
        return {"batch_id": batch_id, "shared_context": shared_context, "items": items}
