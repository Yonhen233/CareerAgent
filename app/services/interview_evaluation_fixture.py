from __future__ import annotations

import json
from typing import Any


class DeterministicInterviewEvaluationLLM:
    """Structured LLM fixture used only by the offline evaluation harness."""

    available = True

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
        if trace_name == "interview_prep.generate_interviewer_questions":
            return json.dumps(self._question_payload(), ensure_ascii=False)
        if trace_name.startswith("interview_agentic_rag.plan."):
            return json.dumps(self._plan_payload(json.loads(user_prompt)), ensure_ascii=False)
        if trace_name.startswith("interview_agentic_rag.generate."):
            return json.dumps(self._answer_payload(json.loads(user_prompt)["items"]), ensure_ascii=False)
        if trace_name.startswith("interview_agentic_rag.verify."):
            payload = json.loads(user_prompt)
            claims = self._verifier_claims(payload)
            return json.dumps(
                {
                    "verdicts": [
                        {
                            "question_id": item["question_id"],
                            "claim_index": item["claim_index"],
                            "supported": True,
                            "normalized_claim_type": (
                                item["claim"].get("claim_type")
                                if item["claim"].get("claim_type") in item["allowed_claim_types"]
                                else item["allowed_claim_types"][0]
                            ),
                            "normalized_evidence_ids": item["current_evidence_ids"],
                            "reason": "离线评测 fixture 的 claim 与引用证据按构造契约一致。",
                        }
                        for item in claims
                    ]
                },
                ensure_ascii=False,
            )
        if trace_name.startswith("interview_agentic_rag.coverage."):
            payload = json.loads(user_prompt)
            return json.dumps(
                {
                    "results": [
                        {
                            "question_id": item["question_id"],
                            "covered": True,
                            "uncovered_claims": [],
                            "reason": "离线评测 fixture 的正文事实均已在 claims 中声明。",
                        }
                        for item in payload["items"]
                    ]
                },
                ensure_ascii=False,
            )
        if trace_name.startswith("interview_agentic_rag.render."):
            payload = json.loads(user_prompt)
            return json.dumps(
                {
                    "answers": [
                        {
                            "question_id": item["question_id"],
                            "reference_answer": (
                                "我会先给出结论，并且只使用已核验事实："
                                + "；".join(claim["text"] for claim in item["verified_claims"])
                                + "。对于证据没有覆盖的细节，我会明确说明边界并作为后续验证项，"
                                "不会包装成已经交付的经历。回答时我会先说明这些事实与当前问题的关系，"
                                "再解释可验证的设计取舍，并主动区分项目中已经完成的内容、一般技术原理和未来方案，"
                                "让面试官能够继续沿着证据追问，而不是依赖听起来完整但无法核验的描述。"
                            ),
                            "answer_framework": [
                                {"section": "结论", "guidance": "直接回应问题。"},
                                {"section": "事实", "guidance": "使用已验证 claims。"},
                                {"section": "边界", "guidance": "说明未覆盖内容。"},
                            ],
                        }
                        for item in payload["items"]
                    ]
                },
                ensure_ascii=False,
            )
        if trace_name.startswith("interview_agentic_rag.repair."):
            payload = json.loads(user_prompt)
            return json.dumps(self._answer_payload(payload["generation_input"]["items"]), ensure_ascii=False)
        raise AssertionError(f"Unsupported interview evaluation trace: {trace_name}")

    def _verifier_claims(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        for group in payload["items"]:
            evidence_by_alias = {
                item["evidence_id"]: item for item in group["available_evidence"]
            }
            for claim in group["claims"]:
                cited = [
                    evidence_by_alias[evidence_id]
                    for evidence_id in claim["current_evidence_ids"]
                    if evidence_id in evidence_by_alias
                ]
                allowed_sets = [set(item["allowed_claim_types"]) for item in cited]
                allowed = sorted(set.intersection(*allowed_sets)) if allowed_sets else []
                flattened.append(
                    {
                        **claim,
                        "question_id": group["question_id"],
                        "allowed_claim_types": allowed,
                    }
                )
        return flattened

    def _question_payload(self) -> dict[str, Any]:
        return {
            "question_sets": [
                {
                    "category": "LLM 项目实现追问",
                    "questions": [
                        {
                            "question": "请结合简历项目说明 Agent 工作流从输入到工具执行和校验的完整链路。",
                            "follow_ups": ["失败如何恢复？", "哪些节点必须人工确认？"],
                            "intent": "检验 Agent 工程实现。",
                            "answer_points": ["状态", "工具", "校验"],
                            "skills": ["Agent", "Workflow"],
                            "risk_level": "low",
                            "source_perspective": "llm_project_implementation",
                        },
                        {
                            "question": "项目中的 RAG 为什么使用混合召回和二阶段重排？",
                            "follow_ups": ["RRF 如何融合？", "如何定位召回和排序问题？"],
                            "intent": "检验 RAG 检索设计。",
                            "answer_points": ["召回", "重排", "评测"],
                            "skills": ["RAG", "Reranker"],
                            "risk_level": "low",
                            "source_perspective": "llm_project_implementation",
                        },
                    ],
                },
                {
                    "category": "LLM 八股与基础追问",
                    "questions": [
                        {
                            "question": "FastAPI 的 async 适合哪些任务，CPU 密集任务应该如何处理？",
                            "follow_ups": ["数据库 Session 如何管理？", "长任务如何返回进度？"],
                            "intent": "检验后端并发基础。",
                            "answer_points": ["IO 并发", "外部队列", "连接管理"],
                            "skills": ["FastAPI"],
                            "risk_level": "medium",
                            "source_perspective": "llm_foundation_drill",
                        },
                        {
                            "question": "如果 JD 中的技能没有项目证据，面试时应该如何回答？",
                            "follow_ups": ["相邻经验如何迁移？", "如何设计最小验证任务？"],
                            "intent": "检验事实边界。",
                            "answer_points": ["诚实披露", "相邻经验", "补齐计划"],
                            "skills": ["Guardrail"],
                            "risk_level": "high",
                            "source_perspective": "llm_foundation_drill",
                        },
                    ],
                },
            ]
        }

    def _plan_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        available = payload.get("available_sources") or {}
        sources = [
            source
            for source in ("resume", "job", "interview_experience", "project_document", "technical_knowledge")
            if int(available.get(source) or 0) > 0
        ]
        return {
            "plans": [
                {
                    "question_id": item["question_id"],
                    "intent": f"检索当前题目的简历、JD 与技术证据：{item['question']}",
                    "answer_mode": "evidence_grounded_interview_answer",
                    "search_queries": [item["question"], *(item.get("skills") or [])[:2]],
                    "target_sources": sources,
                    "required_evidence": [
                        "candidate_experience",
                        "job_requirement",
                        "technical_explanation",
                    ],
                    "forbidden_claims": ["无证据的生产经验、规模和效果数字"],
                    "confidence": 0.95,
                }
                for item in payload["questions"]
            ]
        }

    def _answer_payload(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        answers: list[dict[str, Any]] = []
        for item in items:
            by_source = {evidence["source_type"]: evidence for evidence in item["evidence"]}
            resume = by_source["resume"]
            job = by_source["job"]
            technical = by_source.get("technical_knowledge") or by_source["project_document"]
            claims = [
                {
                    "text": "根据当前简历证据，我能够确认自己参与过其中记录的项目，但只陈述材料里明确出现的个人经历和职责边界。",
                    "claim_type": "candidate_experience",
                    "evidence_ids": [resume["evidence_id"]],
                },
                {
                    "text": "目标岗位的能力要求以当前 JD 证据为准，我会据此调整回答重点，但不会把岗位要求包装成自己的经历。",
                    "claim_type": "job_requirement",
                    "evidence_ids": [job["evidence_id"]],
                },
                {
                    "text": "技术解释会使用审核后的知识或项目文档说明原理、取舍和验证方式，同时明确区分通用知识与本人实践。",
                    "claim_type": "technical_explanation",
                    "evidence_ids": [technical["evidence_id"]],
                },
            ]
            answers.append(
                {
                    "question_id": item["question_id"],
                    "reference_answer": (
                        "我会先直接回答问题，再把个人经历、岗位要求和技术原理分开说明。"
                        f"简历证据显示：{resume['text'][:80]}。这部分可以作为我真实做过的项目边界。"
                        f"当前岗位证据显示：{job['text'][:80]}。它只代表岗位关注点，不能反过来证明我已经掌握。"
                        f"技术上参考：{technical['text'][:100]}。我会据此解释方案、取舍、验证方法和失败排查。"
                        "没有被这些证据支持的生产规模、效果数字或事故经验，我会明确说没有验证，并给出下一步验证计划。"
                    ),
                    "answer_framework": [
                        {"section": "直接回答", "guidance": "先给结论，不回避问题。"},
                        {"section": "引用经历", "guidance": "只使用简历中真实存在的项目证据。"},
                        {"section": "解释取舍", "guidance": "结合 JD 和技术资料说明方案、验证与边界。"},
                    ],
                    "claims": claims,
                    "citations": [
                        {"evidence_id": evidence_id, "claim": claim["text"]}
                        for claim in claims
                        for evidence_id in claim["evidence_ids"]
                    ],
                }
            )
        return {"answers": answers}
