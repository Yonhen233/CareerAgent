from __future__ import annotations

"""动态 Todo DAG 的数据结构和调度逻辑。

Todo 节点保存依赖、前置条件和执行状态，使 Agent 能在执行过程中追加任务、
重排未完成节点，并在恢复时从持久化状态继续，而不是重新猜测已经完成的步骤。
"""

from collections import defaultdict, deque
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any
import asyncio

from app.models.agent_plan_schemas import Action, IntentEnvelope, TaskPlan


class TaskGraphValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Invalid task graph: " + "; ".join(errors))


@dataclass(frozen=True)
class TaskGraphValidation:
    passed: bool
    errors: list[str]
    topological_order: list[str]
    ready_nodes: list[str]


class IntentValidator:
    """Validate LLM intent before it can be compiled or executed."""

    def validate(self, intent: IntentEnvelope, *, available_context: dict[str, Any] | None = None) -> None:
        errors: list[str] = []
        goal_ids = [goal.goal_id for goal in intent.goals]
        if len(goal_ids) != len(set(goal_ids)):
            errors.append("goal_id must be unique")
        known = set(goal_ids)
        forbidden = set(intent.forbidden_actions)
        available = available_context or {}
        goals_by_id = {goal.goal_id: goal for goal in intent.goals}
        for goal in intent.goals:
            unknown_deps = set(goal.depends_on) - known
            if unknown_deps:
                errors.append(f"{goal.goal_id} depends on unknown goals: {sorted(unknown_deps)}")
            if goal.action in forbidden:
                errors.append(f"goal {goal.goal_id} uses forbidden action {goal.action}")
            dependency_actions = {
                goals_by_id[dep].action
                for dep in goal.depends_on
                if dep in goals_by_id
            }
            profile_can_be_created = "create_profile" in dependency_actions
            job_can_be_created = bool(dependency_actions & {"search_jobs", "select_jobs", "create_profile"})
            if goal.action in {"tailor_resume", "prepare_interview", "quick_apply", "match_job"} and not available.get("profile_id") and not profile_can_be_created:
                if "profile" not in intent.missing_context:
                    errors.append(f"goal {goal.goal_id} requires profile context")
            if goal.action in {"tailor_resume", "prepare_interview", "quick_apply", "match_job"} and not available.get("job_id") and not job_can_be_created:
                if goal.action != "match_job" and "job" not in intent.missing_context:
                    errors.append(f"goal {goal.goal_id} requires job context")
        self._assert_acyclic(goal_ids, {goal.goal_id: goal.depends_on for goal in intent.goals}, errors)
        if errors:
            raise TaskGraphValidationError(errors)

    @staticmethod
    def _assert_acyclic(nodes: list[str], dependencies: dict[str, list[str]], errors: list[str]) -> None:
        indegree = {node: len(dependencies.get(node, [])) for node in nodes}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for node, deps in dependencies.items():
            for dep in deps:
                outgoing[dep].append(node)
        queue = deque(node for node, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for child in outgoing[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if visited != len(nodes):
            errors.append("goal dependency graph contains a cycle")


class TaskGraphValidator:
    def validate(self, plan: TaskPlan, *, intent: IntentEnvelope | None = None) -> TaskGraphValidation:
        errors: list[str] = []
        node_ids = [node.node_id for node in plan.nodes]
        if len(node_ids) != len(set(node_ids)):
            errors.append("node_id must be unique")
        known_nodes = set(node_ids)
        goal_ids = {goal.goal_id for goal in intent.goals} if intent else {node.goal_id for node in plan.nodes}
        dependencies: dict[str, list[str]] = {}
        for node in plan.nodes:
            dependencies[node.node_id] = list(node.depends_on)
            if node.goal_id not in goal_ids:
                errors.append(f"node {node.node_id} references unknown goal {node.goal_id}")
            missing = set(node.depends_on) - known_nodes
            if missing:
                errors.append(f"node {node.node_id} depends on unknown nodes: {sorted(missing)}")
            if node.action in plan.forbidden_actions:
                errors.append(f"node {node.node_id} uses forbidden action {node.action}")
            if node.action == "quick_apply" and node.failure_policy == "retry":
                errors.append("high-risk quick_apply cannot use automatic retry")
        order = self._topological_order(node_ids, dependencies, errors)
        if len(plan.nodes) > plan.budget.max_nodes:
            errors.append("plan exceeds max_nodes budget")
        ready = [node_id for node_id in order if not dependencies.get(node_id)]
        result = TaskGraphValidation(not errors, errors, order, ready)
        if errors:
            raise TaskGraphValidationError(errors)
        return result

    @staticmethod
    def _topological_order(nodes: list[str], dependencies: dict[str, list[str]], errors: list[str]) -> list[str]:
        indegree = {node: len(dependencies.get(node, [])) for node in nodes}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for node, deps in dependencies.items():
            for dep in deps:
                outgoing[dep].append(node)
        queue = deque(node for node, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while queue:
            current = queue.popleft()
            order.append(current)
            for child in outgoing[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(order) != len(nodes):
            errors.append("task plan contains a dependency cycle")
        return order


class GlobalPlanner:
    """Compile validated intent into a bounded, auditable task DAG."""

    def compile(self, intent: IntentEnvelope, *, plan_id: str) -> TaskPlan:
        nodes = []
        for index, goal in enumerate(intent.goals, start=1):
            dependencies = [f"n{intent.goals.index(dep) + 1}" for dep in intent.goals if dep.goal_id in goal.depends_on]
            action: Action = goal.action
            nodes.append({
                "node_id": f"n{index}",
                "goal_id": goal.goal_id,
                "action": action,
                "depends_on": dependencies,
                "parallel_group": goal.for_each,
                "for_each": goal.for_each,
                "preconditions": self._preconditions(action),
                "required_outputs": self._outputs(action),
                "completion_criteria": self._completion(action),
                "failure_policy": self._failure_policy(action),
            })
        plan = TaskPlan(
            plan_id=plan_id,
            nodes=nodes,
            forbidden_actions=list(intent.forbidden_actions),
            global_completion_criteria=["所有必需节点完成", "没有违反 forbidden_actions", "最终 Artifact 可追溯"],
        )
        TaskGraphValidator().validate(plan, intent=intent)
        return plan

    @staticmethod
    def _preconditions(action: str) -> list[str]:
        if action in {"tailor_resume", "prepare_interview", "quick_apply"}:
            return ["profile_available", "job_available"]
        if action == "match_job":
            return ["profile_available", "job_or_search_result_available"]
        if action == "select_jobs":
            return ["search_results_available"]
        return []

    @staticmethod
    def _outputs(action: str) -> list[str]:
        return {
            "create_profile": ["profile_id"],
            "search_jobs": ["job_ids"],
            "select_jobs": ["selected_job_ids"],
            "match_job": ["match_result_id"],
            "tailor_resume": ["resume_version_id"],
            "prepare_interview": ["interview_prep_id"],
            "quick_apply": ["application_packet_id"],
            "full_flow": ["flow_result"],
            "clarify": ["clarification_request"],
        }.get(action, [])

    @staticmethod
    def _completion(action: str) -> list[str]:
        if action == "search_jobs":
            return ["返回至少一个岗位或明确记录没有结果"]
        if action == "tailor_resume":
            return ["简历事实门禁通过"]
        if action == "prepare_interview":
            return ["题库和答案证据校验通过"]
        if action == "quick_apply":
            return ["用户审批通过且投递材料可追溯"]
        return ["产生声明的必需输出"]

    @staticmethod
    def _failure_policy(action: str) -> str:
        if action in {"search_jobs", "match_job"}:
            return "retry_or_partial"
        if action in {"tailor_resume", "prepare_interview"}:
            return "partial_success"
        if action == "quick_apply":
            return "fail"
        return "fail"


class TaskGraphRuntime:
    """Serializable DAG scheduler used between LangGraph checkpoints."""

    def __init__(self, plan: TaskPlan, state: dict[str, Any] | None = None) -> None:
        self.plan = plan
        current = state or {}
        self.status: dict[str, str] = {
            node.node_id: "pending" for node in plan.nodes
        }
        for node_id, value in (current.get("status") or {}).items():
            if node_id in self.status and value in {"pending", "ready", "running", "completed", "failed"}:
                self.status[node_id] = value
        self.errors: dict[str, str] = dict(current.get("errors") or {})
        self.attempts: dict[str, int] = {
            node_id: int(value or 0)
            for node_id, value in (current.get("attempts") or {}).items()
            if node_id in self.status
        }
        self.replan_history: list[dict[str, Any]] = list(current.get("replan_history") or [])

    def ready_nodes(self, *, context: dict[str, Any] | None = None) -> list[str]:
        context = context or {}
        ready: list[str] = []
        for node in self.plan.nodes:
            if self.status[node.node_id] != "pending":
                continue
            if not all(self.status.get(dep) == "completed" for dep in node.depends_on):
                continue
            if not self._preconditions_met(node.preconditions, context):
                continue
            self.status[node.node_id] = "ready"
            ready.append(node.node_id)
        return ready

    def ready_batches(self, *, context: dict[str, Any] | None = None) -> list[list[str]]:
        """Return ready work grouped by declared parallel_group."""
        ready = [node_id for node_id, value in self.status.items() if value == "ready"]
        ready.extend(self.ready_nodes(context=context))
        ready = list(dict.fromkeys(ready))
        order = {node.node_id: index for index, node in enumerate(self.plan.nodes)}
        ready.sort(key=lambda node_id: order.get(node_id, len(order)))
        by_group: dict[str, list[str]] = {}
        for node_id in ready:
            node = next(item for item in self.plan.nodes if item.node_id == node_id)
            group = node.parallel_group or f"serial:{node_id}"
            by_group.setdefault(group, []).append(node_id)
        # Execute one scheduling wave at a time.  Nodes explicitly sharing a
        # parallel_group remain together; independent serial nodes are not
        # allowed to leapfrog one another, which keeps business ordering and
        # avoids concurrent writes through the same SQLAlchemy session.
        first_group = next(iter(by_group.values()), [])
        return [first_group] if first_group else []

    def start(self, node_id: str) -> None:
        self._require(node_id, expected={"ready"})
        self.status[node_id] = "running"
        self.attempts[node_id] = self.attempts.get(node_id, 0) + 1

    def complete(self, node_id: str, *, outputs: dict[str, Any] | None = None) -> None:
        self._require(node_id, expected={"running", "ready"})
        self.status[node_id] = "completed"

    async def execute_ready(
        self,
        *,
        context: dict[str, Any] | None = None,
        handler: Callable[[Any], Any | Awaitable[Any]],
    ) -> dict[str, Any]:
        """Execute one dependency-safe batch and persist every node transition."""
        batches = self.ready_batches(context=context)
        if not batches:
            return {"status": "complete" if self.is_complete() else "blocked", "results": {}, "state": self.as_dict()}
        results: dict[str, Any] = {}
        for batch in batches:
            for node_id in batch:
                self.start(node_id)

            async def run_one(node_id: str) -> tuple[str, Any, Exception | None]:
                node = next(item for item in self.plan.nodes if item.node_id == node_id)
                try:
                    value = handler(node)
                    if isinstance(value, Awaitable):
                        value = await value
                    return node_id, value, None
                except Exception as exc:  # noqa: BLE001
                    return node_id, None, exc

            completed = await asyncio.gather(*(run_one(node_id) for node_id in batch))
            for node_id, value, error in completed:
                if error is not None:
                    self.fail(node_id, str(error))
                    continue
                self.complete(node_id, outputs=value if isinstance(value, dict) else None)
                results[node_id] = value
            if self.has_failed():
                break
        return {
            "status": "failed" if self.has_failed() else "completed_batch",
            "results": results,
            "state": self.as_dict(),
        }

    def fail(self, node_id: str, error: str) -> None:
        self._require(node_id, expected={"running", "ready"})
        self.status[node_id] = "failed"
        self.errors[node_id] = str(error)

    def retry(self, node_id: str, *, reason: str) -> None:
        """Put a retryable node back in the scheduler without hiding the failure."""
        self._require(node_id, expected={"running", "failed"})
        self.status[node_id] = "ready"
        self.errors[node_id] = str(reason)

    def replan(self, plan: TaskPlan, *, reason: str) -> dict[str, Any]:
        """Replace the remaining DAG while preserving completed work.

        A re-plan is deliberately conservative: completed nodes must still exist
        with the same action, while pending work may be reordered or removed.
        This makes recovery auditable and prevents a planner from silently
        rewriting already executed side effects.
        """
        TaskGraphValidator().validate(plan)
        old_completed = {
            node.node_id: node.action
            for node in self.plan.nodes
            if self.status.get(node.node_id) == "completed"
        }
        new_by_id = {node.node_id: node for node in plan.nodes}
        incompatible = [
            node_id
            for node_id, action in old_completed.items()
            if node_id not in new_by_id or new_by_id[node_id].action != action
        ]
        if incompatible:
            raise TaskGraphValidationError([
                "re-plan cannot replace completed nodes: " + ", ".join(sorted(incompatible))
            ])
        previous = self.plan.plan_id
        previous_status = dict(self.status)
        self.plan = plan
        self.status = {node.node_id: "pending" for node in plan.nodes}
        self.errors = {}
        self.attempts = {
            node_id: self.attempts.get(node_id, 0)
            for node_id in self.status
        }
        for node_id in old_completed:
            self.status[node_id] = "completed"
        record = {
            "from_plan_id": previous,
            "to_plan_id": plan.plan_id,
            "reason": reason,
            "preserved_completed_nodes": sorted(old_completed),
            "previous_status": previous_status,
        }
        self.replan_history.append(record)
        return record

    def is_complete(self) -> bool:
        return all(value == "completed" for value in self.status.values())

    def has_failed(self) -> bool:
        return any(value == "failed" for value in self.status.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan.plan_id,
            "status": dict(self.status),
            "attempts": dict(self.attempts),
            "ready_nodes": [node_id for node_id, value in self.status.items() if value == "ready"],
            "running_nodes": [node_id for node_id, value in self.status.items() if value == "running"],
            "completed_nodes": [node_id for node_id, value in self.status.items() if value == "completed"],
            "failed_nodes": [node_id for node_id, value in self.status.items() if value == "failed"],
            "errors": dict(self.errors),
            "replan_history": list(self.replan_history),
        }

    def _require(self, node_id: str, *, expected: set[str]) -> None:
        if node_id not in self.status:
            raise TaskGraphValidationError([f"unknown node {node_id}"])
        if self.status[node_id] not in expected:
            raise TaskGraphValidationError([
                f"node {node_id} is {self.status[node_id]}, expected one of {sorted(expected)}"
            ])

    @staticmethod
    def _preconditions_met(preconditions: list[str], context: dict[str, Any]) -> bool:
        return all(
            bool(context.get(item))
            for item in preconditions
            if item not in {"job_or_search_result_available"}
        ) and (
            "job_or_search_result_available" not in preconditions
            or bool(
                context.get("job_id")
                or context.get("search_results_available")
                or context.get("search_attempted")
            )
        )
