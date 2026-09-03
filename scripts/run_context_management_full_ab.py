from __future__ import annotations

import argparse
import asyncio
import io
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.langgraph_orchestrator import LangGraphAgentOrchestrator  # noqa: E402
from app.agents.tools import bind_agent_tool  # noqa: E402
from app.api.jobs import _index_job_chunks  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal, init_db  # noqa: E402
from app.core.llm import LLMClient, llm_trace_context  # noqa: E402
from app.models.entities import (  # noqa: E402
    AgentRun,
    Application,
    ContextCompressionTrace,
    InterviewPrep,
    Job,
    LLMCallLog,
    ResumeVersion,
)
from app.models.schemas import AgentRunRequest  # noqa: E402
from app.services.jd_parser import JDParserService  # noqa: E402
from app.services.resume_parser import ResumeParserService  # noqa: E402


DEFAULT_OUTPUT = ROOT / "data" / "runtime" / "context-management-v3-full-ab.json"

CASES = [
    {
        "case_id": "rag_backend_cn",
        "resume": """姓名：林明\n邮箱：liming@example.com\n求职目标：Agent 开发实习生\n技能：Python、FastAPI、SQLite、RAG、LangGraph、Redis、Docker\n教育经历：华南理工大学，软件工程本科，2023-2027\n项目经历：CareerAgent 求职智能体\n使用 FastAPI 和 LangGraph 构建可中断恢复的工作流；使用 SQLite 保存职位、简历版本和运行轨迹；实现 BM25 与向量检索融合、Top20 重排、引用校验和提示词注入检测。离线评测集包含 240 条中英文查询，Recall@10 为 0.91。\n边界说明：没有 Kubernetes 生产部署经验。""",
        "jd": """职位：Agent 开发实习生\n公司：星河智能\n地点：深圳\n职责：开发面向企业知识库的 RAG Agent；使用 FastAPI 构建异步服务；完善评测、可观测性与故障恢复。\n要求：熟悉 Python、FastAPI、RAG、向量检索、LangGraph；了解 Redis 和 Docker。\n加分项：有提示词注入防护和 Reranker 经验。""",
        "title": "Agent 开发实习生",
        "company": "星河智能",
        "location": "深圳",
        "expected": ["FastAPI", "RAG", "LangGraph"],
    },
    {
        "case_id": "llm_eval_cn",
        "resume": """姓名：周宁\n邮箱：zhouning@example.com\n求职目标：大模型应用评测实习生\n技能：Python、LLM、Evaluation、RAG、SQL、Pydantic\n教育经历：武汉大学，计算机科学本科，2022-2026\n项目经历：LLM 工作流质量平台\n设计 180 条结构化测试样例，覆盖工具调用、引用正确性、事实一致性和早停；使用 Pydantic 校验结构化输出，使用 SQLite 记录 trace 和 token 用量。将关键事实完整率从 86% 提升到 97%。\n边界说明：只在课程项目中接触 MLflow，没有线上运维经验。""",
        "jd": """职位：LLM 应用评测实习生\n公司：云帆科技\n地点：北京\n职责：建设 Agent 与 RAG 评测集，分析 bad case，维护自动化回归门控。\n要求：Python、LLM Evaluation、结构化输出、SQL；理解 RAG 引用和工具调用评测。\n加分项：了解 MLflow，但不要求生产经验。""",
        "title": "LLM 应用评测实习生",
        "company": "云帆科技",
        "location": "北京",
        "expected": ["Python", "Evaluation", "RAG"],
    },
    {
        "case_id": "agent_platform_cn",
        "resume": """姓名：陈曦\n邮箱：chenxi@example.com\n求职目标：Agent 平台研发实习生\n技能：Python、FastAPI、LangGraph、Redis、SQLite、Playwright、Docker\n教育经历：电子科技大学，软件工程硕士，2025-2028\n项目经历：多租户 Agent Runtime\n实现工具注册、参数契约、RBAC 权限检查、审批审计、幂等键和 Redis worker；LangGraph checkpoint 支持中断恢复，并通过 receipt 防止邮件与浏览器动作重复执行。编写 96 个并发和恢复测试。\n边界说明：没有管理大规模 Kubernetes 集群。""",
        "jd": """职位：Agent 平台研发实习生\n公司：瀚海软件\n地点：上海\n职责：开发 Agent Runtime、工具网关、任务队列和运行监控。\n要求：Python、FastAPI、LangGraph、Redis；理解幂等、并发、checkpoint 和权限隔离。\n加分项：Playwright 或容器化经验。""",
        "title": "Agent 平台研发实习生",
        "company": "瀚海软件",
        "location": "上海",
        "expected": ["LangGraph", "Redis", "FastAPI"],
    },
    {
        "case_id": "rag_bilingual",
        "resume": """Name: Alice Wang\nEmail: alice@example.com\nTarget Role: RAG / AI Agent Intern\nSkills: Python, FastAPI, RAG, Embedding, Reranker, PostgreSQL, Docker\nEducation: Tongji University, BEng Computer Science, 2023-2027\nProject: Bilingual Knowledge Assistant\nBuilt Chinese-English hybrid retrieval with BM25, multilingual embeddings and RRF. Added a cross-encoder reranker and citation validation. Evaluated 320 noisy queries and achieved Recall@10 of 0.89.\nBoundary: No production Kubernetes experience.""",
        "jd": """Role: RAG Engineer Intern\nCompany: Northstar AI\nLocation: Shanghai / Remote\nResponsibilities: Build bilingual retrieval pipelines, evaluate embeddings and rerankers, and expose async APIs.\nRequirements: Python, RAG, vector search, FastAPI, evaluation.\nPreferred: Docker and PostgreSQL.""",
        "title": "RAG Engineer Intern",
        "company": "Northstar AI",
        "location": "上海",
        "expected": ["Python", "RAG", "FastAPI"],
    },
    {
        "case_id": "tool_agent_cn",
        "resume": """姓名：苏扬\n邮箱：suyang@example.com\n求职目标：AI Agent 应用开发实习生\n技能：Python、Agent、Tool Calling、MCP、FastAPI、SQLite、Prompt Engineering\n教育经历：西安交通大学，人工智能本科，2023-2027\n项目经历：研究资料 Agent\n实现 Plan-Execute 工作流和 8 个只读工具；工具通过 Pydantic schema 注册，执行前校验租户、参数与权限；失败时记录结构化错误并最多修复一次。构建 150 条工具选择样例，准确率 94%。\n边界说明：邮件发送仅做过本地 SMTP 测试。""",
        "jd": """职位：AI Agent 应用开发实习生\n公司：灵犀数据\n地点：杭州\n职责：开发带 Tool Calling 的研究助手，接入 MCP 服务并建设运行 trace。\n要求：Python、Agent、Tool Calling、FastAPI、Prompt Engineering。\n加分项：MCP、SQLite、Agent 评测经验。""",
        "title": "AI Agent 应用开发实习生",
        "company": "灵犀数据",
        "location": "杭州",
        "expected": ["Agent", "Tool Calling", "FastAPI"],
    },
]


def _pdf_bytes(text: str) -> bytes:
    buffer = io.BytesIO()
    font = "CareerAgentCJK"
    try:
        pdfmetrics.getFont(font)
    except KeyError:
        font_path = Path("C:/Windows/Fonts/simhei.ttf")
        if not font_path.exists():
            raise RuntimeError("Full PDF A/B requires an extractable CJK TrueType font.")
        pdfmetrics.registerFont(TTFont(font, str(font_path)))
    doc = canvas.Canvas(buffer, pagesize=(595, 842))
    doc.setTitle("CareerAgent context evaluation resume")
    y = 800
    for paragraph in text.splitlines():
        lines = [paragraph[index : index + 42] for index in range(0, max(len(paragraph), 1), 42)] or [""]
        for line in lines:
            if y < 55:
                doc.showPage()
                y = 800
            doc.setFont(font, 10.5)
            doc.drawString(48, y, line)
            y -= 17
        y -= 5
    doc.save()
    return buffer.getvalue()


async def _persist_job(db, *, run_id: int, case: dict[str, Any], variant: str) -> Job:
    parser = JDParserService()

    async def operation() -> Job:
        structured = await parser.parse_jd(
            case["jd"],
            title=case["title"],
            company=case["company"],
            location=case["location"],
            db=db,
        )
        job = Job(
            tenant_id="context-eval",
            source="context_full_ab",
            external_id=f"{case['case_id']}:{variant}:{uuid4().hex[:8]}",
            title=case["title"],
            company=case["company"],
            location=case["location"],
            job_type=structured.get("job_type") or "internship",
            apply_url=f"https://example.com/jobs/{case['case_id']}",
            raw_jd_text=case["jd"],
            structured_jd_json=structured,
            source_payload_json={"evaluation": True, "variant": variant},
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        _index_job_chunks(db, job)
        return job

    return await LangGraphAgentOrchestrator().trace.step(
        db,
        run_id=run_id,
        step_name="parse_and_index_jd",
        input_json={
            "raw_jd_text": case["jd"],
            "title": case["title"],
            "company": case["company"],
        },
        tool=bind_agent_tool("job_repository.create_from_jd", operation),
    )


def _logs_for_run(db, run_id: int) -> list[LLMCallLog]:
    return [
        row
        for row in db.query(LLMCallLog).order_by(LLMCallLog.id.asc()).all()
        if int((row.context_json or {}).get("run_id") or 0) == run_id
    ]


def _cost_cny(logs: list[LLMCallLog]) -> float:
    total = 0.0
    for row in logs:
        context = row.context_json or {}
        cached = min(row.prompt_tokens, int(context.get("cached_tokens") or 0))
        uncached = max(0, row.prompt_tokens - cached)
        if "pro" in row.model.lower():
            total += uncached * 3 / 1_000_000 + cached * 0.025 / 1_000_000
            total += row.completion_tokens * 6 / 1_000_000
        else:
            total += uncached * 1 / 1_000_000 + cached * 0.02 / 1_000_000
            total += row.completion_tokens * 2 / 1_000_000
    return round(total, 6)


def _run_metrics(db, *, run: AgentRun, started: float, expected: list[str]) -> dict[str, Any]:
    logs = _logs_for_run(db, run.id)
    version = db.query(ResumeVersion).filter(ResumeVersion.profile_id == run.profile_id, ResumeVersion.job_id == run.job_id).order_by(ResumeVersion.id.desc()).first()
    application = db.query(Application).filter(Application.profile_id == run.profile_id, Application.job_id == run.job_id).order_by(Application.id.desc()).first()
    prep = db.query(InterviewPrep).filter(InterviewPrep.profile_id == run.profile_id, InterviewPrep.job_id == run.job_id).order_by(InterviewPrep.id.desc()).first()
    compression = db.query(ContextCompressionTrace).filter(ContextCompressionTrace.run_id == run.id).all()
    output_text = json.dumps(run.output_json or {}, ensure_ascii=False)
    expected_recall = sum(value.lower() in output_text.lower() for value in expected) / max(len(expected), 1)
    provider_complete = all(
        row.status != "completed" or (row.context_json or {}).get("usage_status") == "provider_reported"
        for row in logs
    )
    completion = ((run.output_json or {}).get("completion_verification") or {}).get("passed") is True
    guardrail = bool(version and (version.verification_json or {}).get("passed"))
    application_gate = bool(application and (application.automation_result_json or {}).get("validation_passed"))
    interview_gate = bool(
        prep
        and ((prep.summary_json or {}).get("agentic_rag") or {}).get("status") == "completed"
        and (prep.coverage_json or {}).get("passed")
    )
    return {
        "run_id": run.id,
        "status": run.status,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "input_tokens": sum(row.prompt_tokens for row in logs),
        "output_tokens": sum(row.completion_tokens for row in logs),
        "total_tokens": sum(row.total_tokens for row in logs),
        "business_calls": len({(row.context_json or {}).get("business_call_id") or f"log:{row.id}" for row in logs}),
        "http_attempts": len(logs),
        "compactor_calls": sum(row.trace_name.startswith("natural_language.conversation_compactor") for row in logs),
        "repair_calls": sum("repair" in row.trace_name.lower() or (row.context_json or {}).get("repair_type") not in {None, "none"} for row in logs),
        "provider_usage_complete": provider_complete and bool(logs),
        "cost_cny": _cost_cny(logs),
        "critical_fact_recall": min([row.critical_fact_recall for row in compression], default=1.0),
        "citation_integrity": float(all(row.quality_gate_passed for row in compression)) if compression else float(guardrail),
        "expected_skill_recall": round(expected_recall, 6),
        "guardrail_passed": guardrail,
        "application_dry_run_passed": application_gate,
        "interview_gate_passed": interview_gate,
        "completion_gate_passed": completion,
        "complete": bool(run.status == "completed" and completion and guardrail and application_gate and interview_gate),
        "error": run.error_message,
        "trace_names": [row.trace_name for row in logs],
    }


async def _run_case(db, *, case: dict[str, Any], variant: str) -> dict[str, Any]:
    settings = get_settings()
    settings.context_management_v3_enabled = variant == "v3"
    settings.context_runtime_v2_enabled = True
    settings.context_runtime_v2_shadow_mode = False
    settings.token_optimization_v2_enabled = True
    settings.token_optimization_shadow_mode = False
    settings.interview_rag_max_questions = 6
    settings.interview_rag_answer_batch_size = 10
    settings.interview_rag_verify_question_batch_size = 10
    orchestrator = LangGraphAgentOrchestrator()
    request = AgentRunRequest(
        task_type="full_career_flow",
        query=case["title"],
        location=case["location"],
        limit=5,
        application_confirmed=True,
    )
    run = orchestrator.queue_run(
        db,
        request,
        tenant_id="context-eval",
        user_id=f"ab-{case['case_id']}",
    )
    started = time.perf_counter()
    try:
        with llm_trace_context(
            run_id=run.id,
            task_type="full_career_flow",
            case_id=case["case_id"],
            ab_variant=variant,
        ):
            parser = ResumeParserService()
            profile = await orchestrator.trace.step(
                db,
                run_id=run.id,
                step_name="parse_pdf_resume",
                input_json={
                    "filename": f"{case['case_id']}-{variant}.pdf",
                    "file_artifact_id": None,
                },
                tool=bind_agent_tool(
                    "resume_parser.create_profile_from_pdf",
                    lambda: parser.create_profile_from_pdf(
                        db,
                        filename=f"{case['case_id']}-{variant}.pdf",
                        file_bytes=_pdf_bytes(case["resume"]),
                    ),
                ),
            )
            profile.tenant_id = "context-eval"
            db.add(profile)
            db.commit()
            job = await _persist_job(db, run_id=run.id, case=case, variant=variant)
        run.profile_id = profile.id
        run.job_id = job.id
        run.input_json = {
            **(run.input_json or {}),
            **AgentRunRequest(
                task_type="full_career_flow",
                profile_id=profile.id,
                job_id=job.id,
                query=case["title"],
                location=case["location"],
                limit=5,
                application_confirmed=True,
            ).model_dump(),
            "ab_variant": variant,
            "case_id": case["case_id"],
        }
        db.add(run)
        db.commit()
        run = await orchestrator.run_existing(db, run.id)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        current = db.query(AgentRun).filter(AgentRun.id == run.id).first() or run
        current.status = "failed"
        current.error_message = f"{type(exc).__name__}: {exc}"
        db.add(current)
        db.commit()
        run = current
    metrics = _run_metrics(db, run=run, started=started, expected=case["expected"])
    orchestrator.trace.add_artifact(
        db,
        run_id=run.id,
        artifact_type="context_management_full_ab_metrics",
        payload={"case_id": case["case_id"], "variant": variant, **metrics},
    )
    return metrics


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "run_count": len(rows),
        "completed_runs": sum(row["complete"] for row in rows),
        "completion_rate": round(sum(row["complete"] for row in rows) / max(len(rows), 1), 6),
        "avg_input_tokens": round(statistics.mean(row["input_tokens"] for row in rows), 3),
        "avg_output_tokens": round(statistics.mean(row["output_tokens"] for row in rows), 3),
        "avg_total_tokens": round(statistics.mean(row["total_tokens"] for row in rows), 3),
        "avg_business_calls": round(statistics.mean(row["business_calls"] for row in rows), 3),
        "avg_latency_ms": round(statistics.mean(row["latency_ms"] for row in rows), 3),
        "total_cost_cny": round(sum(row["cost_cny"] for row in rows), 6),
        "critical_fact_recall": round(statistics.mean(row["critical_fact_recall"] for row in rows), 6),
        "citation_integrity": round(statistics.mean(row["citation_integrity"] for row in rows), 6),
        "provider_usage_complete_rate": round(sum(row["provider_usage_complete"] for row in rows) / max(len(rows), 1), 6),
        "guardrail_pass_rate": round(sum(row["guardrail_passed"] for row in rows) / max(len(rows), 1), 6),
        "application_dry_run_pass_rate": round(sum(row["application_dry_run_passed"] for row in rows) / max(len(rows), 1), 6),
        "interview_gate_pass_rate": round(sum(row["interview_gate_passed"] for row in rows) / max(len(rows), 1), 6),
        "completion_gate_pass_rate": round(sum(row["completion_gate_passed"] for row in rows) / max(len(rows), 1), 6),
        "repair_calls": sum(row["repair_calls"] for row in rows),
    }


async def evaluate(*, limit: int | None = None, case_ids: list[str] | None = None) -> dict[str, Any]:
    init_db()
    client = LLMClient()
    if not client.available:
        raise RuntimeError("Real full-run A/B requires LLM_API_KEY.")
    cases = [case for case in CASES if not case_ids or case["case_id"] in set(case_ids)]
    if limit:
        cases = cases[:limit]
    if not cases:
        raise ValueError(f"No evaluation case matched case_ids={case_ids!r}.")
    db = SessionLocal()
    try:
        results = []
        for case in cases:
            pair = {"case_id": case["case_id"]}
            for variant in ("v2", "v3"):
                pair[variant] = await _run_case(db, case=case, variant=variant)
            results.append(pair)
    finally:
        db.close()
        get_settings().context_management_v3_enabled = True
    v2 = _aggregate([row["v2"] for row in results])
    v3 = _aggregate([row["v3"] for row in results])
    input_reduction = 1 - v3["avg_input_tokens"] / max(v2["avg_input_tokens"], 1)
    return {
        "evaluation": "careeragent-context-management-v2-v3-complete-agent-run-ab",
        "mode": "real_llm",
        "workflow": [
            "generated_pdf_parse",
            "jd_parse_and_index",
            "job_match",
            "resume_evidence_retrieval",
            "resume_tailor",
            "independent_guardrail",
            "application_packet_dry_run",
            "interview_prep",
            "completion_gate",
        ],
        "variants": {
            "v2": {"context_runtime_v2": True, "token_optimization_v2": True, "context_management_v3": False},
            "v3": {"context_runtime_v2": True, "token_optimization_v2": True, "context_management_v3": True},
        },
        "case_count": len(cases),
        "run_count": len(cases) * 2,
        "pairs": results,
        "summary": {"v2": v2, "v3": v3, "v3_input_token_reduction": round(input_reduction, 6)},
        "release_gate": {
            "passed": (
                len(cases) >= 5
                and v3["completion_rate"] == 1.0
                and v3["critical_fact_recall"] == 1.0
                and v3["citation_integrity"] == 1.0
                and v3["provider_usage_complete_rate"] == 1.0
            ),
            "requires_five_pairs": True,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run five complete CareerAgent V2/V3 real-LLM pairs.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(evaluate(limit=args.limit, case_ids=args.case_ids))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": report["summary"], "release_gate": report["release_gate"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
