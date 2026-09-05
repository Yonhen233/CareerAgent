from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.database import SessionLocal, init_db
from app.core.llm import LLMClient
from app.models.entities import LLMCallLog
from app.services.pdf_extraction import PDFExtractionService
from app.services.resume_parser import ResumeParserService


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals" / "complex_resume_corpus" / "manifest.json"
OUTPUT = ROOT / "evals" / "results" / "complex_resume_parser_eval.json"


def compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def contains_any(value: Any, expected: str) -> bool:
    return compact(expected) in compact(value)


async def evaluate(manifest_path: Path, output_path: Path, case_id: str | None = None) -> dict[str, Any]:
    client = LLMClient()
    if not client.available:
        raise RuntimeError("LLM is unavailable; this parser evaluation must not silently use a fallback.")
    init_db()
    parser = ResumeParserService()
    extractor = PDFExtractionService()
    prior = {}
    if output_path.exists():
        try:
            prior = {row["case_id"]: row for row in json.loads(output_path.read_text(encoding="utf-8")).get("cases", [])}
        except (OSError, json.JSONDecodeError, TypeError):
            prior = {}
    cases = [row for key, row in prior.items() if case_id and key != case_id]
    db = SessionLocal()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if case_id:
            manifest = [case for case in manifest if case["id"] == case_id]
        for case in manifest:
            if not case_id and prior.get(case["id"], {}).get("status") == "completed":
                cases.append(prior[case["id"]])
                continue
            pdf_path = ROOT / case["pdf_path"]
            extraction = extractor.extract(filename=pdf_path.name, file_bytes=pdf_path.read_bytes())
            before_id = db.query(LLMCallLog.id).order_by(LLMCallLog.id.desc()).limit(1).scalar() or 0
            started = datetime.now(timezone.utc)
            row: dict[str, Any] = {"case_id": case["id"], "status": "running"}
            try:
                parsed = await parser.parse_structured_resume(extraction.raw_text, db=db)
                expected = case["expected_profile"]
                all_text = json.dumps(parsed, ensure_ascii=False)
                skills = [skill for skill in expected["skills"] if contains_any(parsed.get("skills"), skill)]
                target_roles = contains_any(parsed.get("headline"), expected["target_role"]) or contains_any(parsed.get("target_roles"), expected["target_role"])
                source_gate = parsed.get("quality_gate") or {}
                row.update(
                    {
                        "status": "completed",
                        "name_correct": compact(parsed.get("name")) == compact(expected["name"]),
                        "target_role_recovered": target_roles,
                        "skill_recall": round(len(skills) / max(len(expected["skills"]), 1), 4),
                        "skills_missing": [skill for skill in expected["skills"] if skill not in skills],
                        "experience_count": len(parsed.get("work_experience") or []),
                        "project_count": len(parsed.get("projects") or []),
                        "research_present": contains_any(all_text, "科研经历") or contains_any(all_text, "RESEARCH"),
                        "campus_or_leadership_present": contains_any(all_text, "校园") or contains_any(all_text, "LEADERSHIP"),
                        "critical_fact_grounding": round(sum(compact(fact) in compact(all_text) for fact in case["critical_facts"]) / 3, 4),
                        "quality_gate_passed": source_gate.get("passed") is True,
                        "parser_provenance": parsed.get("parser_provenance"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                row.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            logs = db.query(LLMCallLog).filter(LLMCallLog.id > before_id, LLMCallLog.created_at >= started).all()
            row["llm_calls"] = len(logs)
            row["prompt_tokens"] = sum(int(log.prompt_tokens or 0) for log in logs)
            row["completion_tokens"] = sum(int(log.completion_tokens or 0) for log in logs)
            row["total_tokens"] = sum(int(log.total_tokens or 0) for log in logs)
            cases.append(row)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps({"evaluation_type": "complex_resume_structured_parser", "cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    finally:
        db.close()

    completed = [row for row in cases if row.get("status") == "completed"]
    summary = {
        "case_count": len(cases),
        "completed_count": len(completed),
        "parser_success_rate": round(len(completed) / max(len(cases), 1), 4),
        "name_accuracy": round(sum(row["name_correct"] for row in completed) / max(len(completed), 1), 4),
        "target_role_recovery": round(sum(row["target_role_recovered"] for row in completed) / max(len(completed), 1), 4),
        "mean_skill_recall": round(sum(row["skill_recall"] for row in completed) / max(len(completed), 1), 4),
        "critical_fact_grounding": round(sum(row["critical_fact_grounding"] for row in completed) / max(len(completed), 1), 4),
        "quality_gate_pass_rate": round(sum(row["quality_gate_passed"] for row in completed) / max(len(completed), 1), 4),
        "total_llm_calls": sum(row.get("llm_calls", 0) for row in cases),
        "total_tokens": sum(row.get("total_tokens", 0) for row in cases),
    }
    result = {"evaluation_type": "complex_resume_structured_parser", "generated_at": datetime.now(timezone.utc).isoformat(), "summary": summary, "cases": cases}
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--case-id", type=str, default=None)
    args = parser.parse_args()
    result = asyncio.run(evaluate(args.manifest, args.output, args.case_id))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
