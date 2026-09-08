from __future__ import annotations

"""局部、有界的 ReAct 执行器。

它只用于检索补检、查询改写或生成修复等可恢复的小循环，并受到最大轮数、
预算和终止条件限制；主业务流程仍由 LangGraph 任务图控制，避免无限循环和
不可预测的工具调用。
"""

from dataclasses import dataclass, field
import inspect
from typing import Any, Callable
from uuid import uuid4


@dataclass(frozen=True)
class LoopDecision:
    action: str
    reason_code: str
    should_stop: bool = False


@dataclass
class BoundedLocalReAct:
    """Small deterministic controller for domain-specific observe/act/verify loops."""

    owner_node: str
    allowed_actions: set[str]
    max_attempts: int = 2
    max_no_progress: int = 1
    attempt: int = 0
    no_progress_count: int = 0
    action_history: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    def step(
        self,
        observation: dict[str, Any],
        decide: Callable[[dict[str, Any]], LoopDecision],
        act: Callable[[str, dict[str, Any]], dict[str, Any]],
        verify: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        if self.attempt >= self.max_attempts:
            return self._stop("budget_exhausted", observation)
        decision = decide(observation)
        if decision.action not in self.allowed_actions:
            return self._stop("action_not_allowed", observation, decision=decision)
        if decision.should_stop or decision.action == "stop":
            return self._stop(decision.reason_code, observation, decision=decision)
        if decision.action in self.action_history:
            self.no_progress_count += 1
            if self.no_progress_count > self.max_no_progress:
                return self._stop("no_progress", observation, decision=decision)
        self.attempt += 1
        self.action_history.append(decision.action)
        self.trace.append({
            "event": "local_decision",
            "owner_node": self.owner_node,
            "attempt": self.attempt,
            "observation": observation,
            "decision": {"action": decision.action, "reason_code": decision.reason_code},
        })
        result = act(decision.action, observation)
        verification = verify(result)
        self.trace.append({
            "event": "local_verification",
            "owner_node": self.owner_node,
            "attempt": self.attempt,
            "passed": bool(verification.get("passed")),
            "result": verification,
        })
        if verification.get("passed"):
            return {"status": "completed", "attempt": self.attempt, "result": result, "verification": verification}
        self.no_progress_count = self.no_progress_count + 1 if verification.get("no_progress") else 0
        return {"status": "continue", "attempt": self.attempt, "result": result, "verification": verification}

    def state(self) -> dict[str, Any]:
        return {
            "loop_id": f"loop-{uuid4().hex}",
            "owner_node": self.owner_node,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "executed_actions": list(self.action_history),
            "trace": list(self.trace),
        }

    async def astep(
        self,
        observation: dict[str, Any],
        decide: Callable[[dict[str, Any]], LoopDecision],
        act: Callable[[str, dict[str, Any]], Any],
        verify: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """Async counterpart for retrieval or tool actions that call an LLM."""
        if self.attempt >= self.max_attempts:
            return self._stop("budget_exhausted", observation)
        decision = decide(observation)
        if decision.action not in self.allowed_actions:
            return self._stop("action_not_allowed", observation, decision=decision)
        if decision.should_stop or decision.action == "stop":
            return self._stop(decision.reason_code, observation, decision=decision)
        if decision.action in self.action_history:
            self.no_progress_count += 1
            if self.no_progress_count > self.max_no_progress:
                return self._stop("no_progress", observation, decision=decision)
        self.attempt += 1
        self.action_history.append(decision.action)
        self.trace.append({
            "event": "local_decision",
            "owner_node": self.owner_node,
            "attempt": self.attempt,
            "observation": observation,
            "decision": {"action": decision.action, "reason_code": decision.reason_code},
        })
        result = act(decision.action, observation)
        if inspect.isawaitable(result):
            result = await result
        verification = verify(result)
        self.trace.append({
            "event": "local_verification",
            "owner_node": self.owner_node,
            "attempt": self.attempt,
            "passed": bool(verification.get("passed")),
            "result": verification,
        })
        if verification.get("passed"):
            return {"status": "completed", "attempt": self.attempt, "result": result, "verification": verification}
        self.no_progress_count = self.no_progress_count + 1 if verification.get("no_progress") else 0
        return {"status": "continue", "attempt": self.attempt, "result": result, "verification": verification}

    def _stop(self, reason: str, observation: dict[str, Any], *, decision: LoopDecision | None = None) -> dict[str, Any]:
        payload = {
            "status": "stopped" if reason != "budget_exhausted" else "budget_exhausted",
            "reason": reason,
            "attempt": self.attempt,
            "observation": observation,
        }
        if decision:
            payload["decision"] = {"action": decision.action, "reason_code": decision.reason_code}
        self.trace.append({"event": "local_loop_stopped", **payload})
        return payload
