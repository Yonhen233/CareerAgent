from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient

from app.main import app


DEFAULT_JD = """
岗位：Agent 开发实习生
团队：AI 应用平台
职责：
1. 参与面向真实业务的 Agent workflow、RAG 检索、工具调用和可观测 trace 开发。
2. 使用 Python、FastAPI、SQLite 或向量库实现简历解析、JD 解析、岗位匹配、内容生成和评测链路。
3. 设计 LLM 结构化 JSON 调用、上下文压缩、guardrail、失败 trace 和回归测试。
4. 能结合用户场景优化前端交互和后台任务进度。
要求：
1. 熟悉 Python、FastAPI、SQL/SQLite、RAG、embedding/reranker、LLM API。
2. 有 Agent、Plan-Execute、ReAct repair、评测集或工程化项目经验优先。
3. 能区分真实项目证据、课程学习、计划学习和缺失技能披露。
""".strip()


def _ensure_ok(response, label: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise RuntimeError(f"{label} failed: HTTP {response.status_code} {response.text[:1200]}")
    return response.json()


def _create_profile(client: TestClient, pdf_path: Path) -> dict[str, Any]:
    with pdf_path.open("rb") as file:
        return _ensure_ok(
            client.post(
                "/profiles/upload",
                files={"file": (pdf_path.name, file, "application/pdf")},
            ),
            "profile upload",
        )


def _create_job(client: TestClient) -> dict[str, Any]:
    return _ensure_ok(
        client.post(
            "/jobs",
            json={
                "title": "Agent 开发实习生",
                "company": "DemoAI",
                "location": "深圳",
                "apply_url": "https://example.com/jobs/agent-intern",
                "jd_text": DEFAULT_JD,
            },
        ),
        "job create",
    )


def _run_agent(client: TestClient, payload: dict[str, Any], label: str) -> dict[str, Any]:
    run = _ensure_ok(client.post("/agent/runs", json=payload), label)
    if run["status"] != "completed":
        raise RuntimeError(f"{label} failed: {json.dumps(run.get('output_json'), ensure_ascii=False)[:1200]}")
    return run


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real LLM user-flow smoke test.")
    parser.add_argument("--pdf", default="demo_resumes/agent_intern_strong_resume.pdf")
    args = parser.parse_args()
    pdf_path = ROOT / args.pdf
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    with TestClient(app) as client:
        profile = _create_profile(client, pdf_path)
        job = _create_job(client)
        tailor_run = _run_agent(
            client,
            {
                "task_type": "tailor_resume_for_job",
                "profile_id": profile["id"],
                "job_id": job["id"],
            },
            "tailor resume",
        )
        resume_version_id = tailor_run["output_json"]["resume_version_id"]
        apply_run = _run_agent(
            client,
            {
                "task_type": "quick_apply",
                "profile_id": profile["id"],
                "job_id": job["id"],
                "resume_version_id": resume_version_id,
            },
            "quick apply",
        )
        interview_run = _run_agent(
            client,
            {
                "task_type": "prepare_interview_for_job",
                "profile_id": profile["id"],
                "job_id": job["id"],
            },
            "interview prep",
        )
        logs = _ensure_ok(client.get("/llm/debug/logs?limit=30"), "llm logs")
        stage_logs = [
            {
                "trace_name": row["trace_name"],
                "status": row["status"],
                "latency_ms": row["latency_ms"],
                "stage": (row.get("context_json") or {}).get("stage"),
            }
            for row in logs
        ]
        output = {
            "profile": {
                "id": profile["id"],
                "name": profile.get("name"),
                "skills": (profile.get("structured_profile_json") or {}).get("skills", [])[:10],
            },
            "job": {
                "id": job["id"],
                "title": job["title"],
                "required_skills": (job.get("structured_jd_json") or {}).get("required_skills", [])[:12],
            },
            "runs": {
                "tailor": {
                    "id": tailor_run["id"],
                    "risk_level": tailor_run["output_json"]["verification"].get("risk_level"),
                    "resume_version_id": resume_version_id,
                },
                "quick_apply": {
                    "id": apply_run["id"],
                    "application_id": apply_run["output_json"]["application_id"],
                    "fit_score": apply_run["output_json"]["fit_gate"]["overall_score"],
                },
                "interview": {
                    "id": interview_run["id"],
                    "interview_prep_id": interview_run["output_json"]["interview_prep_id"],
                    "question_set_count": interview_run["output_json"]["question_set_count"],
                    "gap_drill_count": interview_run["output_json"]["gap_drill_count"],
                },
            },
            "llm_logs": stage_logs[:12],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
