from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.schemas import TaskState  # noqa: E402
from app.services.context_runtime import ContextIntegrityError  # noqa: E402
from app.services.conversation_compactor import ConversationCompactor  # noqa: E402
from app.services.task_state import TaskStateReducer, TaskStateValidationError  # noqa: E402


DEFAULT_DATASET = ROOT / "evals" / "conversation_task_state_cases.json"
DEFAULT_OUTPUT = ROOT / "data" / "runtime" / "conversation-task-state-offline.json"


class DeterministicSummaryLLM:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(self, **kwargs):
        self.calls += 1
        payload = json.loads(kwargs["user_prompt"])
        state = payload["current_task_state"]
        messages = payload["messages"]
        changes = state.get("corrections") or []
        history_text = "；".join(
            f"历史上 {item['field']} 曾从 {item['old_value']} 改为 {item['new_value']}"
            for item in changes
        )
        return {
            "discussion_summary": history_text or "讨论了岗位选择理由和后续问题。",
            "rationales": ["围绕 Agent 岗位和真实项目证据筛选"],
            "unresolved_questions": [],
            "source_message_ids": [item["message_id"] for item in messages],
            "task_state_version": state["version"],
            "task_state_claims": {
                "target_role": state["target_role"],
                "location": state["location"],
                "forbidden_actions": state["forbidden_actions"],
                "completed_actions": state["completed_actions"],
            },
            "historical_changes": changes,
            "authoritative": False,
        }


async def evaluate_case(case: dict) -> dict:
    reducer = TaskStateReducer()
    state = TaskState()
    rejected_non_user = 0
    user_turns = 0
    for turn in case["turns"]:
        if turn["role"] != "user":
            try:
                reducer.merge(
                    state,
                    turn.get("state_updates"),
                    source_message_id=turn["message_id"],
                    source_role=turn["role"],
                    source_text=turn["content"],
                )
            except TaskStateValidationError:
                rejected_non_user += 1
            continue
        user_turns += 1
        state = reducer.merge(
            state,
            turn.get("state_updates"),
            source_message_id=turn["message_id"],
            source_role="user",
            source_text=turn["content"],
        )

    expected = case["expected"]
    field_checks = {
        "goal": state.goal == expected["goal"],
        "target_role": state.target_role == expected["target_role"],
        "location": state.location == expected["location"],
        "constraints": set(state.constraints) == set(expected["constraints"]),
        "selected_actions": set(state.selected_actions) == set(expected["selected_actions"]),
        "pending_actions": set(state.pending_actions) == set(expected["pending_actions"]),
    }
    expected_forbidden = set(expected["forbidden_actions"])
    forbidden_recall = (
        len(expected_forbidden.intersection(state.forbidden_actions)) / len(expected_forbidden)
        if expected_forbidden
        else float(not state.forbidden_actions)
    )
    correction_expected = case["scenario"] in {
        "target_role_change",
        "location_change",
        "mixed_zh_en",
        "old_new_conflict",
        "ellipsis_reference",
    }
    correction_effective = (not correction_expected) or bool(state.corrections)

    checkpoint_payload = json.loads(state.model_dump_json())
    restored = TaskState.model_validate(checkpoint_payload)
    checkpoint_consistent = restored.model_dump() == state.model_dump()

    llm = DeterministicSummaryLLM()
    compactor = ConversationCompactor(llm)
    history = case["history_messages"] if case["force_compaction"] else case["history_messages"][-6:]
    before_state = state.model_dump()
    compaction = await compactor.compact_if_needed(
        None,
        run_id=None,
        messages=history,
        node_budget_tokens=1000,
        task_state=state,
    )
    state_retained = state.model_dump() == before_state

    conflict_rejected = True
    if compaction.summary:
        conflicting = deepcopy(compaction.summary)
        conflicting["task_state_claims"]["location"] = "不存在的冲突地点"
        try:
            compactor._validate_summary(
                conflicting,
                compactor._normalize(history[:-6]),
                task_state=state,
            )
            conflict_rejected = False
        except ContextIntegrityError:
            pass

    return {
        "case_id": case["case_id"],
        "scenario": case["scenario"],
        "passed": (
            all(field_checks.values())
            and forbidden_recall == 1.0
            and correction_effective
            and checkpoint_consistent
            and state_retained
            and conflict_rejected
            and rejected_non_user == sum(1 for turn in case["turns"] if turn["role"] != "user")
            and compaction.compactor_called == bool(case["force_compaction"])
        ),
        "field_checks": field_checks,
        "forbidden_recall": forbidden_recall,
        "correction_effective": correction_effective,
        "checkpoint_consistent": checkpoint_consistent,
        "non_user_updates_rejected": rejected_non_user,
        "state_retained_after_compaction": state_retained,
        "summary_conflict_rejected": conflict_rejected,
        "compactor_called": compaction.compactor_called,
        "compactor_attempts": compaction.compactor_attempts,
        "estimated_input_tokens_before": compaction.original_tokens,
        "estimated_input_tokens_after": compaction.final_tokens,
        "ordinary_planner_calls_expected": user_turns,
        "offline_provider_calls": 0,
        "final_task_state": state.model_dump(),
    }


async def run(dataset_path: Path) -> dict:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    results = [await evaluate_case(case) for case in dataset["cases"]]
    compacted = [item for item in results if item["compactor_called"]]
    field_values = [
        value for item in results for value in item["field_checks"].values()
    ]
    summary = {
        "case_count": len(results),
        "passed_cases": sum(item["passed"] for item in results),
        "pass_rate": sum(item["passed"] for item in results) / len(results),
        "goal_constraint_field_accuracy": sum(field_values) / len(field_values),
        "correction_effectiveness_rate": statistics.mean(
            float(item["correction_effective"]) for item in results
        ),
        "forbidden_action_recall": statistics.mean(item["forbidden_recall"] for item in results),
        "compaction_state_retention_rate": statistics.mean(
            float(item["state_retained_after_compaction"]) for item in compacted
        ),
        "summary_conflict_accept_rate": 1
        - statistics.mean(float(item["summary_conflict_rejected"]) for item in compacted),
        "checkpoint_recovery_consistency_rate": statistics.mean(
            float(item["checkpoint_consistent"]) for item in results
        ),
        "compactor_triggered_cases": len(compacted),
        "compactor_calls": sum(item["compactor_attempts"] for item in results),
        "ordinary_planner_calls_expected": sum(
            item["ordinary_planner_calls_expected"] for item in results
        ),
        "offline_provider_calls": 0,
        "avg_estimated_tokens_before_compaction": statistics.mean(
            item["estimated_input_tokens_before"] for item in compacted
        ),
        "avg_estimated_tokens_after_compaction": statistics.mean(
            item["estimated_input_tokens_after"] for item in compacted
        ),
        "token_metric_type": "offline_estimate_not_provider_usage",
    }
    return {
        "evaluation": "careeragent-conversation-task-state-offline-v1",
        "mode": "offline_deterministic",
        "dataset": str(dataset_path.relative_to(ROOT)),
        "summary": summary,
        "release_gate": {
            "passed": (
                summary["pass_rate"] == 1.0
                and summary["forbidden_action_recall"] == 1.0
                and summary["checkpoint_recovery_consistency_rate"] == 1.0
                and summary["summary_conflict_accept_rate"] == 0.0
            )
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = asyncio.run(run(args.dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if not report["release_gate"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
