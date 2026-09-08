from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Action = Literal[
    "create_profile",
    "search_jobs",
    "select_jobs",
    "match_job",
    "tailor_resume",
    "prepare_interview",
    "quick_apply",
    "full_flow",
    "clarify",
]

FailurePolicy = Literal[
    "fail",
    "retry",
    "retry_or_partial",
    "partial_success",
    "clarify",
    "replan",
]


class Goal(BaseModel):
    goal_id: str = Field(min_length=1, max_length=80)
    action: Action
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    for_each: str | None = None

    @field_validator("depends_on")
    @classmethod
    def unique_dependencies(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item and item.strip()))


class IntentEnvelope(BaseModel):
    goals: list[Goal] = Field(default_factory=list, min_length=1, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=30)
    forbidden_actions: list[str] = Field(default_factory=list, max_length=20)
    required_context: list[str] = Field(default_factory=list, max_length=20)
    missing_context: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    clarification_required: bool = False


class TaskNode(BaseModel):
    node_id: str = Field(min_length=1, max_length=80)
    goal_id: str = Field(min_length=1, max_length=80)
    action: Action
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: str | None = None
    for_each: str | None = None
    preconditions: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    failure_policy: FailurePolicy = "fail"


class TaskBudget(BaseModel):
    max_nodes: int = Field(default=20, ge=1, le=100)
    max_replans: int = Field(default=2, ge=0, le=5)
    max_local_react_loops: int = Field(default=6, ge=0, le=20)


class TaskPlan(BaseModel):
    plan_id: str = Field(min_length=1, max_length=100)
    nodes: list[TaskNode] = Field(default_factory=list, min_length=1, max_length=100)
    global_completion_criteria: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    budget: TaskBudget = Field(default_factory=TaskBudget)


class LocalLoopState(BaseModel):
    loop_id: str
    owner_node: str
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=2, ge=0, le=10)
    observation: dict[str, Any] = Field(default_factory=dict)
    decision: dict[str, Any] = Field(default_factory=dict)
    executed_actions: list[str] = Field(default_factory=list)
    termination_status: Literal["running", "stopped", "failed", "budget_exhausted"] = "running"
    termination_reason: str | None = None
