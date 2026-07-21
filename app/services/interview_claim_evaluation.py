from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMCallBudget, llm_call_budget, llm_trace_context
from app.models.entities import EvaluationRun
from app.services.interview_agentic_rag import (
    SOURCE_CLAIM_POLICY,
    InterviewAgenticRAGError,
    InterviewAgenticRAGService,
)


class InterviewClaimVerifierEvaluationService:
    """Evaluate the production claim verifier without running the full interview workflow."""

    MIN_POSITIVE_RECALL = 0.90
    MAX_FALSE_POSITIVE_RATE = 0.10

    def __init__(self, *, llm: Any | None = None) -> None:
        self.settings = get_settings()
        self.verifier = InterviewAgenticRAGService(llm=llm)

    async def run(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        if not self.verifier.llm.available:
            raise InterviewAgenticRAGError(
                "Interview claim verifier evaluation requires a configured LLM."
            )

        path = dataset_path or self.settings.base_path / "evals" / "interview_claim_verifier_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        if not cases:
            raise ValueError("Interview claim verifier evaluation dataset is empty.")

        questions, evidence, answers = self._build_inputs(cases)
        workflow_run_id = f"claim-verifier-{uuid.uuid4().hex[:12]}"
        budget = LLMCallBudget(
            name="interview_claim_verifier_evaluation",
            max_calls=2,
            max_prompt_chars=20000,
            max_completion_tokens=2800,
        )
        self.verifier.settings = self.verifier.settings.model_copy(
            update={
                "interview_rag_verify_question_batch_size": min(len(cases), 7),
                "interview_rag_verify_max_tokens": 1400,
                "interview_rag_json_repair_attempts": 0,
            }
        )
        with llm_trace_context(
            workflow="interview_claim_verifier_evaluation",
            workflow_run_id=workflow_run_id,
            dataset=path.name,
            stage="claim_verification",
        ):
            with llm_call_budget(budget):
                classified, errors = await self.verifier._verify_claim_entailment(
                    db,
                    questions=questions,
                    evidence=evidence,
                    answers=answers,
                    trace_prefix="evaluation.interview_claim_verifier",
                )

        case_results = self._case_results(cases, classified=classified, errors=errors)
        summary = self._summary(
            path=path,
            workflow_run_id=workflow_run_id,
            budget=budget,
            case_results=case_results,
        )
        run = EvaluationRun(
            name="interview_claim_verifier_evaluation",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def _build_inputs(
        self, cases: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
        questions: list[dict[str, Any]] = []
        evidence: dict[str, list[dict[str, Any]]] = {}
        answers: dict[str, dict[str, Any]] = {}
        for case in cases:
            question_id = str(case["id"])
            questions.append({"question_id": question_id, "question": case["question"]})
            evidence[question_id] = [
                {
                    "evidence_id": item["id"],
                    "source_type": item["source_type"],
                    "text": item["text"],
                    "allowed_claim_types": sorted(SOURCE_CLAIM_POLICY[item["source_type"]]),
                }
                for item in case["evidence"]
            ]
            answers[question_id] = {
                "question_id": question_id,
                "claims": [
                    {
                        "text": case["claim"],
                        "claim_type": case["claim_type"],
                        "evidence_ids": list(case["cited_evidence_ids"]),
                    }
                ],
                "citations": [],
            }
        return questions, evidence, answers

    def _case_results(
        self,
        cases: list[dict[str, Any]],
        *,
        classified: dict[str, dict[str, Any]],
        errors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        error_by_question = {
            str(item.get("question_id") or ""): item for item in errors if item.get("question_id")
        }
        errors_by_question: dict[str, list[dict[str, Any]]] = {}
        for item in errors:
            question_id = str(item.get("question_id") or "")
            if question_id:
                errors_by_question.setdefault(question_id, []).append(item)
        results: list[dict[str, Any]] = []
        for case in cases:
            question_id = str(case["id"])
            claims = classified[question_id].get("claims") or []
            actual_supported = len(claims) == 1
            actual_claim_type = str(claims[0].get("claim_type") or "") if claims else None
            expected_supported = bool(case["expected_supported"])
            expected_type = str(case["expected_claim_type"])
            expected_answered = bool(case.get("expected_answered", expected_supported))
            actual_answered = not any(
                item.get("code") == "answer_not_responsive"
                for item in errors_by_question.get(question_id, [])
            )
            type_correct = not actual_supported or actual_claim_type == expected_type
            passed = (
                actual_supported == expected_supported
                and type_correct
                and actual_answered == expected_answered
            )
            results.append(
                {
                    "case_id": question_id,
                    "category": case["category"],
                    "question": case["question"],
                    "claim": case["claim"],
                    "expected_supported": expected_supported,
                    "actual_supported": actual_supported,
                    "expected_claim_type": expected_type,
                    "actual_claim_type": actual_claim_type,
                    "expected_answered": expected_answered,
                    "actual_answered": actual_answered,
                    "actual_evidence_ids": list(claims[0].get("evidence_ids") or []) if claims else [],
                    "passed": passed,
                    "error": error_by_question.get(question_id),
                }
            )
        return results

    def _summary(
        self,
        *,
        path: Path,
        workflow_run_id: str,
        budget: LLMCallBudget,
        case_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        positives = [item for item in case_results if item["expected_supported"]]
        negatives = [item for item in case_results if not item["expected_supported"]]
        true_positives = sum(item["actual_supported"] for item in positives)
        true_negatives = sum(not item["actual_supported"] for item in negatives)
        false_positives = len(negatives) - true_negatives
        false_negatives = len(positives) - true_positives
        expected_nonresponsive = [
            item for item in case_results if not item["expected_answered"]
        ]
        answer_correct = sum(
            item["actual_answered"] == item["expected_answered"] for item in case_results
        )
        nonresponsive_false_accepts = sum(
            item["actual_answered"] for item in expected_nonresponsive
        )

        category_breakdown: dict[str, dict[str, Any]] = {}
        for category in sorted({str(item["category"]) for item in case_results}):
            rows = [item for item in case_results if item["category"] == category]
            category_breakdown[category] = {
                "case_count": len(rows),
                "passed_count": sum(item["passed"] for item in rows),
                "pass_rate": self._ratio(sum(item["passed"] for item in rows), len(rows)),
            }

        positive_recall = self._ratio(true_positives, len(positives))
        false_positive_rate = self._ratio(false_positives, len(negatives))
        strategy_recall = category_breakdown.get("strategy_supported", {}).get("pass_rate", 0.0)
        disguised_false_positive_rate = self._ratio(
            sum(
                item["actual_supported"]
                for item in case_results
                if item["category"] == "disguised_experience"
            ),
            sum(item["category"] == "disguised_experience" for item in case_results),
        )
        question_answering_accuracy = self._ratio(answer_correct, len(case_results))
        nonresponsive_false_accept_rate = self._ratio(
            nonresponsive_false_accepts,
            len(expected_nonresponsive),
        )
        release_gate = {
            "min_positive_recall": self.MIN_POSITIVE_RECALL,
            "max_false_positive_rate": self.MAX_FALSE_POSITIVE_RATE,
            "requires_all_strategy_cases": True,
            "requires_zero_disguised_experience_false_positives": True,
            "requires_zero_nonresponsive_answer_false_accepts": True,
            "requires_perfect_question_answering_accuracy": True,
        }
        passed = (
            positive_recall >= self.MIN_POSITIVE_RECALL
            and false_positive_rate <= self.MAX_FALSE_POSITIVE_RATE
            and strategy_recall == 1.0
            and disguised_false_positive_rate == 0.0
            and nonresponsive_false_accept_rate == 0.0
            and question_answering_accuracy == 1.0
        )
        return {
            "evaluation_type": "interview_claim_verifier",
            "dataset": str(path),
            "workflow_run_id": workflow_run_id,
            "case_count": len(case_results),
            "passed_count": sum(item["passed"] for item in case_results),
            "accuracy": self._ratio(sum(item["passed"] for item in case_results), len(case_results)),
            "positive_recall": positive_recall,
            "specificity": self._ratio(true_negatives, len(negatives)),
            "false_positive_rate": false_positive_rate,
            "false_negative_rate": self._ratio(false_negatives, len(positives)),
            "strategy_recall": strategy_recall,
            "disguised_experience_false_positive_rate": disguised_false_positive_rate,
            "question_answering_accuracy": question_answering_accuracy,
            "nonresponsive_answer_false_accept_rate": nonresponsive_false_accept_rate,
            "category_breakdown": category_breakdown,
            "release_gate": release_gate,
            "passed": passed,
            "llm_budget": budget.to_dict(),
        }

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0
