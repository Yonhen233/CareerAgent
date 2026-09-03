from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.natural_language import NaturalLanguageAgentService  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal, init_db  # noqa: E402
from app.core.llm import LLMClient, llm_trace_context  # noqa: E402
from app.models.entities import AgentRun, LLMCallLog  # noqa: E402
from app.models.schemas import NaturalLanguageAgentRequest, TaskState  # noqa: E402
from app.services.context_runtime import TokenEstimator  # noqa: E402
from app.services.conversation_compactor import ConversationCompactionResult  # noqa: E402
from app.services.task_state import TaskStateReducer  # noqa: E402


DEFAULT_OUTPUT = ROOT / "data" / "runtime" / "conversation-task-state-real-ab.json"

CASES = [
    {
        "case_id": "location_correction_cn",
        "state": {"version": 2, "goal": "寻找 Agent 实习", "target_role": "Agent 开发", "location": "北京"},
        "instruction": "地点从北京改成深圳，目标改为 RAG Agent，只搜索岗位，不要自动投递。",
        "expected": {"target_role": "RAG Agent", "location": "深圳", "forbidden": ["auto_apply"]},
    },
    {
        "case_id": "draft_and_email_guard_cn",
        "state": {"version": 3, "goal": "寻找 Agent 实习", "target_role": "Agent 平台开发", "location": "上海"},
        "instruction": "继续看上海的 Agent 平台岗位，只生成草稿，不要发送邮件，地点保持不变。",
        "expected": {"target_role": "Agent 平台开发", "location": "上海", "forbidden": ["external_send", "email_send"]},
    },
    {
        "case_id": "bilingual_role_location",
        "state": {"version": 2, "goal": "Find an Agent internship", "target_role": "LLM App Intern", "location": "上海"},
        "instruction": "Change target to AI Agent Engineer and location to Remote. Do not automatically apply.",
        "expected": {"target_role": "AI Agent Engineer", "location": "Remote", "forbidden": ["auto_apply"]},
    },
    {
        "case_id": "explicit_permission_removal",
        "state": {
            "version": 4,
            "goal": "寻找 Agent 实习",
            "target_role": "Agent Evaluation",
            "location": "杭州",
            "forbidden_actions": ["auto_apply"],
        },
        "instruction": "岗位和地点都保持不变，现在允许自动投递，但真正外发前仍要确认。",
        "expected": {"target_role": "Agent Evaluation", "location": "杭州", "forbidden": ["unapproved_high_risk_action"]},
    },
    {
        "case_id": "ellipsis_and_confirmation",
        "state": {"version": 3, "goal": "寻找 Agent 实习", "target_role": "Tool Agent", "location": "深圳"},
        "instruction": "刚才那个地点改成杭州，岗位方向不变；任何投递都必须经过我确认。",
        "expected": {"target_role": "Tool Agent", "location": "杭州", "forbidden": ["unapproved_high_risk_action"]},
    },
]


def _history(case_id: str, state: TaskState) -> list[dict]:
    messages = []
    for index in range(1, 15):
        content = (
            f"{case_id} 第 {index} 轮：讨论 {state.target_role} 的岗位范围、项目证据、城市偏好和选择理由。"
            "用户强调所有经历必须来自真实简历，助手只解释检索结果，不得替用户做高风险决定。"
        ) * 35
        messages.append(
            {
                "message_id": f"{case_id}-h{index}",
                "role": "user" if index % 2 else "assistant",
                "content": content,
            }
        )
    return messages


class RawConversationBypass:
    def __init__(self) -> None:
        self.estimator = TokenEstimator()

    async def compact_if_needed(self, db, *, run_id, messages, node_budget_tokens, task_state=None):
        normalized = [
            {
                "message_id": str(item.get("message_id") or f"m{index + 1}"),
                "role": str(item.get("role") or "user"),
                "content": str(item.get("content") or ""),
                "critical_facts": [],
            }
            for index, item in enumerate(messages)
            if str(item.get("content") or "").strip()
        ]
        tokens = self.estimator.count(normalized).tokens
        return ConversationCompactionResult(
            recent_messages=normalized,
            summary=None,
            summary_artifact_id=None,
            compactor_called=False,
            compactor_attempts=0,
            fallback_to_raw=False,
            validation_errors=[],
            original_tokens=tokens,
            final_tokens=tokens,
        )


def _usage(rows: list[LLMCallLog]) -> dict:
    prompt = sum(row.prompt_tokens for row in rows)
    completion = sum(row.completion_tokens for row in rows)
    total = sum(row.total_tokens for row in rows)
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": total,
        "business_calls": len({(row.context_json or {}).get("business_call_id") for row in rows}),
        "http_attempts": len(rows),
        "provider_usage_complete": all(
            (row.context_json or {}).get("usage_status") == "provider_reported" and row.total_tokens > 0
            for row in rows
        ),
        "trace_names": [row.trace_name for row in rows],
        "compactor_calls": sum("conversation_compactor" in row.trace_name for row in rows),
        "planner_calls": sum(row.trace_name == "natural_language.plan" for row in rows),
    }


async def _run_variant(db, case: dict, variant: str) -> dict:
    state = TaskState.model_validate(case["state"])
    run = AgentRun(
        tenant_id="conversation-real-eval",
        user_id="eval-user",
        task_type="natural_language_request",
        status="running",
        input_json={"case_id": case["case_id"], "variant": variant},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    service = NaturalLanguageAgentService(llm=LLMClient())
    if variant == "raw_history":
        service.conversation_compactor = RawConversationBypass()
    request = NaturalLanguageAgentRequest(
        instruction=case["instruction"],
        message_id=f"{case['case_id']}-current",
        task_state=state,
        conversation_messages=_history(case["case_id"], state),
        query=state.target_role,
        location=state.location,
    )
    started = time.perf_counter()
    with llm_trace_context(
        workflow="conversation_task_state_real_ab",
        workflow_run_id=str(run.id),
        run_id=run.id,
        agent_run_id=run.id,
        variant=variant,
    ):
        plan = await service._build_plan(db, request, run_id=run.id)
    final_state = TaskStateReducer().merge(
        state,
        plan.get("state_updates"),
        source_message_id=request.message_id,
        source_role="user",
        source_text=request.instruction,
    )
    rows = [
        row
        for row in db.query(LLMCallLog).order_by(LLMCallLog.id.asc()).all()
        if int((row.context_json or {}).get("run_id") or 0) == run.id
    ]
    expected = case["expected"]
    expected_forbidden = set(expected["forbidden"])
    forbidden_recall = (
        len(expected_forbidden.intersection(final_state.forbidden_actions)) / len(expected_forbidden)
        if expected_forbidden
        else float(not final_state.forbidden_actions)
    )
    changed_fields = [
        field
        for field in ("target_role", "location")
        if str(getattr(state, field) or "") != str(expected[field])
    ]
    correction_effective = all(
        any(correction.field == field for correction in final_state.corrections)
        for field in changed_fields
    )
    result = {
        "run_id": run.id,
        "variant": variant,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        **_usage(rows),
        "compactor_triggered": bool(
            (plan.get("context_management") or {}).get("conversation_compactor_called")
        ),
        "compactor_attempts": int(
            (plan.get("context_management") or {}).get("conversation_compactor_attempts") or 0
        ),
        "field_accuracy": statistics.mean(
            [
                float(final_state.target_role == expected["target_role"]),
                float(final_state.location == expected["location"]),
            ]
        ),
        "forbidden_action_recall": forbidden_recall,
        "correction_effective": correction_effective,
        "summary_validation_error_count": len(
            (plan.get("context_management") or {}).get("conversation_validation_errors") or []
        ),
        "state_updates": plan.get("state_updates") or {},
        "final_task_state": final_state.model_dump(),
        "model_names": sorted({row.model for row in rows}),
    }
    result["passed"] = (
        result["field_accuracy"] == 1.0
        and forbidden_recall == 1.0
        and correction_effective
        and result["provider_usage_complete"]
        and result["planner_calls"] == 1
        and (
            (variant == "compressed" and result["compactor_triggered"] and result["compactor_calls"] >= 1)
            or (variant == "raw_history" and not result["compactor_triggered"] and result["compactor_calls"] == 0)
        )
    )
    run.status = "completed" if result["passed"] else "failed"
    run.output_json = result
    db.add(run)
    db.commit()
    return result


async def run(
    *,
    case_ids: list[str] | None = None,
    base_report: Path | None = None,
    reuse_latest: bool = False,
) -> dict:
    settings = get_settings()
    if not reuse_latest and not settings.llm_api_key:
        raise RuntimeError("Real conversation A/B requires LLM_API_KEY through the process environment.")
    if not reuse_latest and (settings.llm_model != "deepseek-v4-flash" or settings.llm_routing_enabled):
        raise RuntimeError("Set LLM_MODEL=deepseek-v4-flash and LLM_ROUTING_ENABLED=false for a fixed-model A/B.")
    init_db()
    selected = [case for case in CASES if not case_ids or case["case_id"] in set(case_ids)]
    if case_ids and len(selected) != len(set(case_ids)):
        known = {case["case_id"] for case in CASES}
        raise ValueError(f"Unknown case IDs: {sorted(set(case_ids) - known)}")
    pairs = []
    with SessionLocal() as db:
        for case in selected:
            if reuse_latest:
                matching_runs = [
                    row
                    for row in db.query(AgentRun).order_by(AgentRun.id.desc()).all()
                    if (row.input_json or {}).get("case_id") == case["case_id"]
                ]
                by_variant = {}
                for row in matching_runs:
                    variant = (row.input_json or {}).get("variant")
                    if variant not in by_variant and row.output_json:
                        by_variant[variant] = row.output_json
                if set(by_variant) != {"raw_history", "compressed"}:
                    raise RuntimeError(f"No complete reusable pair for {case['case_id']}.")
                raw = by_variant["raw_history"]
                compressed = by_variant["compressed"]
            else:
                raw = await _run_variant(db, case, "raw_history")
                compressed = await _run_variant(db, case, "compressed")
            pairs.append({"case_id": case["case_id"], "raw_history": raw, "compressed": compressed})
    provenance = {"aggregation_mode": "single_execution_batch"}
    if base_report is not None:
        base = json.loads(base_report.read_text(encoding="utf-8"))
        replacements = {pair["case_id"]: pair for pair in pairs}
        pairs = [replacements.get(pair["case_id"], pair) for pair in base["pairs"]]
        provenance = {
            "aggregation_mode": "base_complete_pairs_plus_targeted_complete_pair_regression",
            "base_report": str(base_report.resolve().relative_to(ROOT)),
            "replaced_case_ids": sorted(replacements),
        }
    case_map = {case["case_id"]: case for case in CASES}
    for pair in pairs:
        case = case_map[pair["case_id"]]
        initial = TaskState.model_validate(case["state"])
        changed_fields = [
            field
            for field in ("target_role", "location")
            if str(getattr(initial, field) or "") != str(case["expected"][field])
        ]
        for variant in ("raw_history", "compressed"):
            result = pair[variant]
            corrections = {
                str(item.get("field"))
                for item in (result.get("final_task_state") or {}).get("corrections") or []
            }
            result["correction_effective"] = all(field in corrections for field in changed_fields)
            result.setdefault(
                "summary_validation_error_count",
                max(int(result.get("compactor_attempts") or 0) - 1, 0),
            )
    raw_runs = [pair["raw_history"] for pair in pairs]
    compressed_runs = [pair["compressed"] for pair in pairs]
    raw_input = statistics.mean(item["input_tokens"] for item in raw_runs)
    compressed_input = statistics.mean(item["input_tokens"] for item in compressed_runs)
    summary = {
        "case_count": len(pairs),
        "run_count": len(pairs) * 2,
        "raw_pass_rate": statistics.mean(float(item["passed"]) for item in raw_runs),
        "compressed_pass_rate": statistics.mean(float(item["passed"]) for item in compressed_runs),
        "compressed_compactor_trigger_rate": statistics.mean(
            float(item["compactor_triggered"]) for item in compressed_runs
        ),
        "raw_avg_input_tokens": raw_input,
        "compressed_avg_input_tokens_including_compactor": compressed_input,
        "actual_input_token_change": (
            (compressed_input - raw_input) / raw_input if raw_input else None
        ),
        "raw_avg_total_tokens": statistics.mean(item["total_tokens"] for item in raw_runs),
        "compressed_avg_total_tokens": statistics.mean(
            item["total_tokens"] for item in compressed_runs
        ),
        "raw_avg_business_calls": statistics.mean(item["business_calls"] for item in raw_runs),
        "compressed_avg_business_calls": statistics.mean(
            item["business_calls"] for item in compressed_runs
        ),
        "compressed_field_accuracy": statistics.mean(
            item["field_accuracy"] for item in compressed_runs
        ),
        "compressed_forbidden_action_recall": statistics.mean(
            item["forbidden_action_recall"] for item in compressed_runs
        ),
        "compressed_correction_effectiveness_rate": statistics.mean(
            float(item.get("correction_effective", True)) for item in compressed_runs
        ),
        "compressed_summary_validation_error_rate": statistics.mean(
            float(bool(item.get("summary_validation_error_count", 0)))
            for item in compressed_runs
        ),
        "provider_usage_complete_rate": statistics.mean(
            float(item["provider_usage_complete"]) for item in [*raw_runs, *compressed_runs]
        ),
        "token_metric_type": "provider_reported",
    }
    return {
        "evaluation": "careeragent-conversation-task-state-real-ab-v1",
        "mode": "real_llm",
        "model": settings.llm_model,
        "pairs": pairs,
        "summary": summary,
        "provenance": provenance,
        "release_gate": {
            "passed": (
                summary["raw_pass_rate"] == 1.0
                and summary["compressed_pass_rate"] == 1.0
                and summary["compressed_compactor_trigger_rate"] == 1.0
                and summary["compressed_forbidden_action_recall"] == 1.0
                and summary["compressed_correction_effectiveness_rate"] == 1.0
                and summary["compressed_summary_validation_error_rate"] == 0.0
                and summary["provider_usage_complete_rate"] == 1.0
            )
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--base-report", type=Path)
    parser.add_argument("--reuse-latest", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(
        run(
            case_ids=args.case_id or None,
            base_report=args.base_report,
            reuse_latest=args.reuse_latest,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if not report["release_gate"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
