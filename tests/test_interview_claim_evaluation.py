import asyncio
import json
from typing import Any

from app.services.interview_claim_evaluation import InterviewClaimVerifierEvaluationService


class SemanticVerifierFixture:
    available = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, Any] | None = None,
        db: Any = None,
        trace_name: str,
    ) -> str:
        del system_prompt, temperature, max_tokens, response_format, db
        self.calls.append(trace_name)
        verdicts = []
        for item in json.loads(user_prompt)["items"]:
            supported = item["question_id"].startswith(("strategy_", "supported_", "relevance_"))
            claim = item["claims"][0]
            verdicts.append(
                {
                    "question_id": item["question_id"],
                    "claim_index": 0,
                    "supported": supported,
                    "normalized_claim_type": claim["claim"]["claim_type"],
                    "normalized_evidence_ids": claim["current_evidence_ids"] if supported else [],
                    "reason": "" if supported else "证据不能证明候选人经历或指标。",
                }
            )
        return json.dumps(
            {
                "verdicts": verdicts,
                "answer_checks": [
                    {
                        "question_id": item["question_id"],
                        "answered": item["question_id"].startswith(("strategy_", "supported_")),
                        "missing_points": (
                            []
                            if item["question_id"].startswith(("strategy_", "supported_"))
                            else ["问题核心要求"]
                        ),
                        "reason": (
                            ""
                            if item["question_id"].startswith(("strategy_", "supported_"))
                            else "没有通过支持性校验的 claim，或事实没有回答当前问题。"
                        ),
                    }
                    for item in json.loads(user_prompt)["items"]
                ],
            },
            ensure_ascii=False,
        )


def test_interview_claim_verifier_evaluation_isolated_release_gate(db_session):
    llm = SemanticVerifierFixture()

    run = asyncio.run(InterviewClaimVerifierEvaluationService(llm=llm).run(db_session))

    assert run.summary_json["evaluation_type"] == "interview_claim_verifier"
    assert run.summary_json["case_count"] == 14
    assert run.summary_json["accuracy"] == 1.0
    assert run.summary_json["positive_recall"] == 1.0
    assert run.summary_json["false_positive_rate"] == 0.0
    assert run.summary_json["strategy_recall"] == 1.0
    assert run.summary_json["disguised_experience_false_positive_rate"] == 0.0
    assert run.summary_json["question_answering_accuracy"] == 1.0
    assert run.summary_json["nonresponsive_answer_false_accept_rate"] == 0.0
    assert run.summary_json["passed"] is True
    assert run.summary_json["llm_budget"]["limits"]["max_calls"] == 2
    assert run.summary_json["llm_budget"]["reserved"]["calls"] == 0
    assert llm.calls == [
        "evaluation.interview_claim_verifier.1",
        "evaluation.interview_claim_verifier.2",
    ]
    assert all(item["passed"] for item in run.case_results_json)
