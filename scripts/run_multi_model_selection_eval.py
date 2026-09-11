"""Run isolated CareerAgent business slices against several model endpoints.

Credentials are read from the environment/.env and never written to artifacts.
Each model has its own SQLite database and checkpoint file. Model routing and
fallback are disabled so results cannot silently mix candidate models.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS = ["DeepSeek-V4-Flash", "DeepSeek-V4-Pro", "Qwen3.5-Plus", "GLM-5.2", "Kimi-K2.5"]


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="并行运行 CareerAgent 多模型业务选型评测。")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--core-token-budget", type=int, default=80000)
    parser.add_argument("--interview-token-budget", type=int, default=60000)
    parser.add_argument("--output-dir", default="evals/results/model_selection_20260912")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


async def probe(base_url: str, key: str, model: str) -> dict[str, Any]:
    url = base_url.rstrip("/") + ("/chat/completions" if base_url.rstrip("/").endswith("/v1") else "/v1/chat/completions")
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with exactly OK."}],
                    "temperature": 0,
                    "max_tokens": 32,
                    "thinking": {"type": "disabled"},
                },
            )
        payload = response.json()
        return {
            "available": response.status_code == 200 and bool(payload.get("choices")),
            "status_code": response.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": payload.get("error"),
        }
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


async def run_model(model: str, *, base_url: str, key: str, out: Path, semaphore: asyncio.Semaphore, options: argparse.Namespace) -> dict[str, Any]:
    async with semaphore:
        safe = slug(model)
        model_out = out / safe
        model_out.mkdir(parents=True, exist_ok=True)
        availability = await probe(base_url, key, model)
        print(json.dumps({"model": model, "stage": "probe", **availability}, ensure_ascii=False), flush=True)
        result: dict[str, Any] = {"model": model, "base_url": base_url, "probe": availability, "slices": {}}
        if not availability.get("available"):
            return result
        env = dict(os.environ)
        env.update({
            "LLM_API_KEY": key,
            "LLM_BASE_URL": base_url,
            "LLM_MODEL": model,
            "LLM_ROUTING_ENABLED": "false",
            "LLM_THINKING_MODE": "disabled",
            "LLM_FALLBACK_ENABLED": "false",
            "REDIS_ENABLED": "false",
            "DATABASE_URL": "sqlite:///" + str(model_out / "business.sqlite").replace("\\", "/"),
            "LANGGRAPH_CHECKPOINT_FILE": str(model_out / "checkpoints.sqlite"),
            "PYTHONIOENCODING": "utf-8",
        })
        for mode, budget in [("core", options.core_token_budget), ("interview", options.interview_token_budget)]:
            output = model_out / f"{mode}.json"
            started = time.perf_counter()
            process = await asyncio.create_subprocess_exec(
                sys.executable, str(ROOT / "scripts" / "run_model_comparison_slice.py"),
                "--model", model, "--mode", mode, "--base-url", base_url,
                "--output", str(output), "--token-budget", str(budget), "--allow-quality-failures",
                cwd=ROOT, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            (model_out / f"{mode}.stdout.log").write_bytes(stdout)
            (model_out / f"{mode}.stderr.log").write_bytes(stderr)
            payload = read_json(output)
            if not payload:
                payload = {"status": "runner_failed", "error": stderr.decode("utf-8", errors="replace")[-2000:]}
            payload["process_exit_code"] = process.returncode
            result["slices"][mode] = payload
            print(json.dumps({"model": model, "stage": mode, "status": payload.get("status"), "elapsed_s": round(time.perf_counter() - started, 2), "tokens": (payload.get("usage") or {}).get("total_tokens")}, ensure_ascii=False), flush=True)
        (model_out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    core = result.get("slices", {}).get("core", {})
    interview = result.get("slices", {}).get("interview", {})
    suites = core.get("suites") or {}
    plan = (suites.get("natural_language_plan") or {}).get("summary") or {}
    jd = (suites.get("jd_parser") or {}).get("summary") or {}
    workflow = (suites.get("workflow") or {}).get("summary") or {}
    prep = ((interview.get("suites") or {}).get("interview") or {}).get("summary") or {}
    usage = [core.get("usage") or {}, interview.get("usage") or {}]
    failed_calls = sum(x.get("failed_call_count", 0) for x in usage)
    runner_failed = any((slice_payload or {}).get("status") in {"failed", "runner_failed"} for slice_payload in (core, interview))
    return {
        "model": result["model"], "base_url": result["base_url"],
        "available": bool((result.get("probe") or {}).get("available")),
        "business_compatible": bool((result.get("probe") or {}).get("available")) and failed_calls == 0 and not runner_failed,
        "core_status": core.get("status"), "interview_status": interview.get("status"),
        "planner_pass_rate": plan.get("pass_rate"),
        "jd_pass_rate": jd.get("pass_rate"), "jd_required_skill_f1": jd.get("avg_required_skill_f1"),
        "workflow_pass_rate": workflow.get("end_to_end_pass_rate"),
        "fit_label_accuracy": workflow.get("fit_label_accuracy"),
        "tailor_pass_rate": workflow.get("tailor_pass_rate"),
        "forbidden_claim_free_rate": workflow.get("forbidden_claim_free_rate"),
        "interview_pass_rate": prep.get("pass_rate"),
        "interview_question_quality": prep.get("avg_question_quality_score"),
        "interview_skill_coverage": prep.get("avg_required_skill_coverage_rate"),
        "call_count": sum(x.get("call_count", 0) for x in usage),
        "failed_call_count": failed_calls,
        "retry_call_count": sum(x.get("retry_call_count", 0) for x in usage),
        "repair_call_count": sum(x.get("repair_call_count", 0) for x in usage),
        "prompt_tokens": sum(x.get("prompt_tokens", 0) for x in usage),
        "completion_tokens": sum(x.get("completion_tokens", 0) for x in usage),
        "total_tokens": sum(x.get("total_tokens", 0) for x in usage),
        "provider_latency_ms": sum(x.get("provider_latency_ms", 0) for x in usage),
        "core_wall_time_ms": core.get("wall_time_ms"),
        "interview_wall_time_ms": interview.get("wall_time_ms"),
        "cost_cny": None,
        "cost_note": "Gateway tariff is not configured; token usage is measured, monetary cost is not inferred.",
    }


async def main(options: argparse.Namespace) -> int:
    config = dotenv_values(ROOT / ".env")
    key = os.getenv("LLM_API_KEY") or config.get("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = options.base_url or os.getenv("LLM_BASE_URL") or config.get("LLM_BASE_URL")
    if not key or not base_url:
        raise RuntimeError("需要配置 LLM_API_KEY 和 LLM_BASE_URL。")
    out = (ROOT / options.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    models = list(dict.fromkeys(x.strip() for x in options.models.split(",") if x.strip()))
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(max(1, options.concurrency))
    results = await asyncio.gather(*[run_model(model, base_url=base_url, key=key, out=out, semaphore=semaphore, options=options) for model in models])
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    payload = {
        "experiment": "careeragent_model_selection_20260912", "git_revision": revision,
        "methodology": {
            "same_gateway": True, "routing_enabled": False, "fallback_enabled": False, "thinking_mode": "disabled",
            "core_cases": {"planner": [0, 5, 10, 19], "jd_parser": [1, 15, 21, 29], "workflow": [19, 21, 23]},
            "interview_cases": [0], "isolated_database_per_model": True,
            "note": "固定业务切片；这是选型初筛，非模型总体能力排名。面试结果仅一个 case，不能外推为总体胜率。",
        },
        "wall_time_ms": round((time.perf_counter() - started) * 1000),
        "summary": [summarize(result) for result in results], "results": results,
    }
    (out / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out / "comparison.json"), "summary": payload["summary"]}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main(args())))
