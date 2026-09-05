from __future__ import annotations

import argparse
import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from app.core.database import SessionLocal
from app.core.llm import llm_trace_context
from app.models.entities import EvaluationRun, LLMCallLog, Profile
from app.services.embedding_service import EmbeddingService
from app.services.job_search_intent import JobSearchIntentService


ROLE_DESCRIPTIONS = {
    "rag_backend": "RAG 检索增强生成工程师，负责知识库、文档解析、混合检索、向量数据库、重排和后端 API",
    "agent_runtime": "AI Agent 平台开发，负责工具调用、工作流编排、状态持久化、故障恢复、人工审批和可观测性",
    "llm_eval": "大模型应用评测工程师，负责测试集、事实一致性、Agent 工具调用和任务完成率评估",
    "ai_product": "AI 产品经理或产品实习生，负责用户需求、智能客服工作流、原型和质量指标",
    "python_junior": "初级 Python 后端或 Web 开发岗位，要求 Python 基础和基础网页能力",
    "computer_vision": "计算机视觉算法岗位，负责目标检测、图像分割和 PyTorch 训练",
}


def _cosine(left: list[float], right: list[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


async def run(cases_path: Path) -> dict:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    db = SessionLocal()
    evaluation = EvaluationRun(
        name=f"profile-query-real-{datetime.now(timezone.utc).isoformat()}",
        summary_json={"status": "running"},
        case_results_json=[],
    )
    db.add(evaluation)
    db.commit()
    db.refresh(evaluation)
    planner = JobSearchIntentService()
    results = []
    try:
        for case in cases:
            profile_data = case["profile"]
            profile = Profile(
                name=case["name"],
                source_type="evaluation",
                raw_resume_text=json.dumps(profile_data, ensure_ascii=False),
                target_roles_json=[],
                structured_profile_json=profile_data,
            )
            with llm_trace_context(
                evaluation_run_id=evaluation.id,
                case_name=case["name"],
                stage="profile_query_planning",
            ):
                intent = await planner.plan(
                    db,
                    preference="",
                    profile=profile,
                    explicit_location=None,
                )
            results.append({"case": case, "intent": intent.as_dict()})

        query_texts = [item["intent"]["retrieval_query"] for item in results]
        role_names = list(ROLE_DESCRIPTIONS)
        batch = EmbeddingService().embed_texts(query_texts + [ROLE_DESCRIPTIONS[name] for name in role_names])
        role_offset = len(query_texts)
        reciprocal_ranks = []
        unsupported = 0
        for index, result in enumerate(results):
            ranking = sorted(
                (
                    {
                        "role": role_name,
                        "score": round(_cosine(batch.vectors[index], batch.vectors[role_offset + role_index]), 6),
                    }
                    for role_index, role_name in enumerate(role_names)
                ),
                key=lambda row: row["score"],
                reverse=True,
            )
            expected = result["case"]["expected_role"]
            rank = next(position for position, row in enumerate(ranking, start=1) if row["role"] == expected)
            reciprocal_ranks.append(1.0 / rank)
            query_lower = " ".join(result["intent"]["query_variants"]).lower()
            violations = [
                term for term in result["case"].get("forbidden_query_terms", []) if term.lower() in query_lower
            ]
            unsupported += int(bool(violations))
            result.update(
                {
                    "expected_role": expected,
                    "expected_rank": rank,
                    "top_roles": ranking[:3],
                    "forbidden_term_violations": violations,
                }
            )
            result.pop("case", None)

        logs = (
            db.query(LLMCallLog)
            .filter(LLMCallLog.context_json["evaluation_run_id"].as_integer() == evaluation.id)
            .all()
        )
        summary = {
            "status": "completed",
            "case_count": len(results),
            "top1_role_accuracy": round(sum(row["expected_rank"] == 1 for row in results) / len(results), 4),
            "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4),
            "unsupported_query_rate": round(unsupported / len(results), 4),
            "llm_completed_calls": sum(row.status == "completed" for row in logs),
            "llm_retry_count": sum(row.status == "retryable_failed" for row in logs),
            "total_tokens": sum(row.total_tokens for row in logs),
            "embedding_provider": batch.provider,
        }
        evaluation.summary_json = summary
        evaluation.case_results_json = results
        db.add(evaluation)
        db.commit()
        return {"evaluation_run_id": evaluation.id, "summary": summary, "cases": results}
    except Exception as exc:
        evaluation.summary_json = {"status": "failed", "error": f"{exc.__class__.__name__}: {exc}"}
        db.add(evaluation)
        db.commit()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="evals/profile_query_cases.json")
    parser.add_argument("--output")
    args = parser.parse_args()
    payload = asyncio.run(run(Path(args.cases)))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
