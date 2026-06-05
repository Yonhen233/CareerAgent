import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import EvaluationRun, Job
from app.models.schemas import GuidedProfileRequest
from app.services.jd_parser import JDParserService
from app.services.matcher import MatcherService
from app.services.resume_parser import ResumeParserService
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


class EvaluationService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.matcher = MatcherService()

    async def run_sample_evaluation(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "sample_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        case_results = []
        for case in cases:
            case_results.append(await self._run_case(db, case))
        summary = self._summarize(case_results)
        run = EvaluationRun(
            name=path.name,
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    async def _run_case(self, db: Session, case: dict[str, Any]) -> dict[str, Any]:
        profile_payload = GuidedProfileRequest.model_validate(case["profile"])
        profile = ResumeParserService().create_profile_from_guided_answers(db, profile_payload)

        job_payload = case["job"]
        jd = await JDParserService().parse_jd(
            job_payload["jd_text"],
            title=job_payload.get("title"),
            company=job_payload.get("company"),
        )
        job = Job(
            source="eval",
            external_id=f"eval:{case['name']}:{profile.id}",
            title=job_payload.get("title") or jd.get("title") or "Eval Job",
            company=job_payload.get("company"),
            raw_jd_text=job_payload["jd_text"],
            structured_jd_json=jd,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        jd_chunks = ResumeTextSplitter().split_jd_text(job.raw_jd_text, job.structured_jd_json, prefix=f"eval_job_{job.id}")
        SQLiteVectorIndex().upsert_job_chunks(db, job.id, jd_chunks)

        match = self.matcher.create_match_result(db, profile, job)
        expected_matched = set(case.get("expected_matched_skills", []))
        expected_missing = set(case.get("expected_missing_skills", []))
        predicted_matched = set(match.matched_skills_json)
        predicted_missing = set(match.missing_skills_json)

        result = {
            "name": case["name"],
            "profile_id": profile.id,
            "job_id": job.id,
            "match_result_id": match.id,
            "overall_score": match.overall_score,
            "required_skill_precision": self._precision(predicted_matched, expected_matched),
            "required_skill_recall": self._recall(predicted_matched, expected_matched),
            "missing_skill_precision": self._precision(predicted_missing, expected_missing),
            "evidence_hit_rate": self._evidence_hit_rate(
                match.relevant_evidence_json,
                case.get("expected_evidence_keywords", []),
            ),
            "score_floor_passed": match.overall_score >= case.get("min_overall_score", 0),
            "score_ceiling_passed": match.overall_score <= case.get("max_overall_score", 100),
            "predicted_matched_skills": match.matched_skills_json,
            "predicted_missing_skills": match.missing_skills_json,
        }
        result["case_passed"] = (
            result["required_skill_recall"] >= 0.6
            and result["missing_skill_precision"] >= 0.5
            and result["score_floor_passed"]
            and result["score_ceiling_passed"]
        )
        return result

    def _summarize(self, case_results: list[dict[str, Any]]) -> dict[str, Any]:
        count = max(len(case_results), 1)
        return {
            "case_count": len(case_results),
            "pass_rate": round(sum(1 for item in case_results if item["case_passed"]) / count, 4),
            "avg_overall_score": round(sum(item["overall_score"] for item in case_results) / count, 2),
            "avg_required_skill_precision": round(
                sum(item["required_skill_precision"] for item in case_results) / count,
                4,
            ),
            "avg_required_skill_recall": round(
                sum(item["required_skill_recall"] for item in case_results) / count,
                4,
            ),
            "avg_missing_skill_precision": round(
                sum(item["missing_skill_precision"] for item in case_results) / count,
                4,
            ),
            "avg_evidence_hit_rate": round(sum(item["evidence_hit_rate"] for item in case_results) / count, 4),
        }

    def _precision(self, predicted: set[str], expected: set[str]) -> float:
        if not predicted:
            return 1.0 if not expected else 0.0
        return round(len(predicted & expected) / len(predicted), 4)

    def _recall(self, predicted: set[str], expected: set[str]) -> float:
        if not expected:
            return 1.0
        return round(len(predicted & expected) / len(expected), 4)

    def _evidence_hit_rate(self, evidence: list[dict[str, Any]], expected_keywords: list[str]) -> float:
        if not expected_keywords:
            return 1.0
        evidence_text = "\n".join(str(item.get("text") or "") for item in evidence).lower()
        hits = [keyword for keyword in expected_keywords if keyword.lower() in evidence_text]
        return round(len(hits) / len(expected_keywords), 4)
