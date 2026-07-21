import asyncio
import json
from collections import Counter

import pytest

from app.agents.orchestrator import AgentOrchestrator
from app.api.interview_prep import _interview_prep_response, list_interview_preps
from app.core.llm import LLMConfigurationError
from app.models.entities import InterviewPrep, Job, Profile
from app.models.schemas import AgentRunRequest
from app.services.interview_answer_framework import InterviewAnswerFrameworkService
from app.services.interview_agentic_rag import (
    SOURCE_CLAIM_POLICY,
    InterviewAgenticRAGError,
    InterviewAgenticRAGService,
)
from app.services.interview_delivery import InterviewPrepDeliveryService
from app.services.interview_experience import InterviewExperienceService
from app.services.interview_prep import InterviewPrepService
from app.services.interview_references import InterviewReferenceService
from app.services.matcher import MatcherService
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


def _verifier_claims(payload):
    flattened = []
    for group in payload["items"]:
        evidence_by_alias = {
            item["evidence_id"]: item for item in group["available_evidence"]
        }
        for item in group["claims"]:
            cited = [
                evidence_by_alias[evidence_id]
                for evidence_id in item["current_evidence_ids"]
                if evidence_id in evidence_by_alias
            ]
            allowed_sets = [set(SOURCE_CLAIM_POLICY[value["source_type"]]) for value in cited]
            allowed = sorted(set.intersection(*allowed_sets)) if allowed_sets else []
            flattened.append(
                {
                    **item,
                    "question_id": group["question_id"],
                    "question": group.get("question", ""),
                    "available_evidence": group["available_evidence"],
                    "allowed_claim_types": allowed,
                }
            )
    return flattened


class FakeAgenticInterviewLLM:
    def __init__(self):
        self.calls = []

    @property
    def available(self):
        return True

    async def generate_text(
        self,
        *,
        system_prompt,
        user_prompt,
        temperature,
        max_tokens,
        response_format=None,
        db,
        trace_name,
    ):
        self.calls.append(
            {
                "trace_name": trace_name,
                "prompt_chars": len(system_prompt) + len(user_prompt),
                "max_tokens": int(max_tokens or 0),
            }
        )
        del temperature, response_format, db
        if trace_name == "interview_prep.generate_interviewer_questions":
            return json.dumps(
                {
                    "question_sets": [
                        {
                            "category": "LLM 项目实现追问",
                            "questions": [
                                {
                                    "question": "CareerAgent 的 RAG 检索从 query 到 evidence 的链路怎么设计？",
                                    "follow_ups": ["Top20 为什么需要 reranker？", "SQLite 和向量库如何分工？"],
                                    "intent": "验证项目实现细节。",
                                    "answer_points": ["讲混合检索", "讲证据边界", "讲 trace"],
                                    "skills": ["RAG", "SQLite"],
                                    "risk_level": "low",
                                    "source_perspective": "llm_project_implementation",
                                },
                                {
                                    "question": "FastAPI 接口并发请求下如何记录 LLM trace？",
                                    "follow_ups": ["失败时如何恢复？", "如何避免日志泄露 key？"],
                                    "intent": "验证工程落地。",
                                    "answer_points": ["讲请求边界", "讲日志字段", "讲脱敏"],
                                    "skills": ["FastAPI", "Agent Trace"],
                                    "risk_level": "low",
                                    "source_perspective": "llm_project_implementation",
                                },
                            ],
                        },
                        {
                            "category": "LLM 八股与基础追问",
                            "questions": [
                                {
                                    "question": "RAG 的召回率和答案质量分别怎么评估？",
                                    "follow_ups": ["如何构造负例？", "如何判断 evidence 是否支持回答？"],
                                    "intent": "覆盖基础八股。",
                                    "answer_points": ["召回", "精排", "引用校验"],
                                    "skills": ["RAG", "Evaluation"],
                                    "risk_level": "medium",
                                    "source_perspective": "llm_foundation_drill",
                                },
                                {
                                    "question": "如果 MLflow 没有生产经验，面试时如何诚实说明？",
                                    "follow_ups": ["相邻经验是什么？", "三天内如何补一个 demo？"],
                                    "intent": "验证缺口披露。",
                                    "answer_points": ["承认缺口", "迁移评测经验", "给补齐计划"],
                                    "skills": ["MLflow"],
                                    "risk_level": "high",
                                    "source_perspective": "llm_foundation_drill",
                                },
                            ],
                        },
                    ]
                },
                ensure_ascii=False,
            )
        if trace_name.startswith("interview_agentic_rag.plan."):
            payload = json.loads(user_prompt)
            return json.dumps(
                {
                    "plans": [
                        {
                            "question_id": item["question_id"],
                            "intent": f"为 {item['question']} 检索候选人、岗位和技术证据",
                            "answer_mode": "evidence_grounded_interview_answer",
                            "search_queries": [item["question"], *(item.get("skills") or [])[:2]],
                            "target_sources": [
                                source
                                for source in (
                                    "resume",
                                    "job",
                                    "interview_experience",
                                    "project_document",
                                    "technical_knowledge",
                                )
                                if payload["available_sources"].get(source, 0) > 0
                            ],
                            "required_evidence": [
                                "candidate_experience",
                                "job_requirement",
                                "technical_explanation",
                            ],
                            "forbidden_claims": ["无证据的生产规模和效果数字"],
                            "confidence": 0.94,
                        }
                        for item in payload["questions"]
                    ]
                },
                ensure_ascii=False,
            )
        if trace_name.startswith("interview_agentic_rag.generate."):
            return self._answer_payload(json.loads(user_prompt)["items"])
        if trace_name.startswith("interview_agentic_rag.verify."):
            payload = json.loads(user_prompt)
            claims = _verifier_claims(payload)
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
                            "reason": "测试证据明确支持该 claim。",
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
                            "reason": "测试回答中的事实均已声明。",
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
                                "我会先直接回答问题，并且只使用已经核验的事实。"
                                + "；".join(claim["text"] for claim in item["verified_claims"])
                                + "。对于证据没有覆盖的选型理由、规模或生产经验，我会明确说明需要结合本人实际补充，"
                                "不会把一般建议包装成已经完成的经历。"
                            ),
                            "answer_framework": [
                                {"section": "直接结论", "guidance": "先回应问题本身。"},
                                {"section": "已验证事实", "guidance": "只引用已通过证据校验的 claims。"},
                                {"section": "能力边界", "guidance": "未被证据覆盖的内容明确说明。"},
                            ],
                        }
                        for item in payload["items"]
                    ]
                },
                ensure_ascii=False,
            )
        if trace_name.startswith("interview_agentic_rag.repair."):
            return self._answer_payload(json.loads(user_prompt)["generation_input"]["items"])
        raise AssertionError(f"Unexpected LLM trace: {trace_name}")

    def _answer_payload(self, items):
        answers = []
        for item in items:
            by_source = {evidence["source_type"]: evidence for evidence in item["evidence"]}
            resume = by_source["resume"]
            assert "job" in by_source, {
                "plan": item["retrieval_plan"],
                "evidence": [(value["source_type"], value["evidence_id"]) for value in item["evidence"]],
            }
            job = by_source["job"]
            technical = by_source.get("technical_knowledge") or by_source["project_document"]
            reference_answer = (
                "我会先直接说明结论：这道题需要把候选人的真实项目、当前岗位要求和技术原理分开回答，不能把 JD 写成自己的经历。"
                f"在简历证据中，我可以引用“{resume['text'][:70]}”，说明我实际做过的边界。"
                f"当前岗位证据是“{job['text'][:70]}”，它只代表岗位关注点，我会据此调整回答重点。"
                f"技术上可以引用“{technical['text'][:90]}”，解释方案、取舍和验证方式。"
                "最后我会主动说明没有经过证据支持的规模、指标和生产经验，并给出下一步如何验证，而不是为了回答完整而补造事实。"
            )
            claims = [
                {
                    "text": "根据当前简历证据，我能够确认自己参与过其中记录的项目，但只陈述材料里明确出现的个人经历和职责边界。",
                    "claim_type": "candidate_experience",
                    "evidence_ids": [resume["evidence_id"]],
                },
                {
                    "text": "当前岗位的能力要求以检索到的 JD 原文为准，我会用它调整回答重点，但不会把岗位要求说成自己已经完成的经历。",
                    "claim_type": "job_requirement",
                    "evidence_ids": [job["evidence_id"]],
                },
                {
                    "text": "在解释技术方案时，我会引用检索到的技术资料说明原理、适用边界和验证方法，并明确区分通用知识与本人实践。",
                    "claim_type": "technical_explanation",
                    "evidence_ids": [technical["evidence_id"]],
                },
            ]
            answers.append(
                {
                    "question_id": item["question_id"],
                    "reference_answer": reference_answer,
                    "answer_framework": [
                        {"section": "直接结论", "guidance": "先回答问题本身，并明确适用边界。"},
                        {"section": "项目证据", "guidance": "引用简历中真实存在的项目实现。"},
                        {"section": "岗位与原理", "guidance": "结合 JD 关注点解释技术取舍和验证方式。"},
                    ],
                    "claims": claims,
                    "citations": [
                        {"evidence_id": evidence_id, "claim": claim["text"]}
                        for claim in claims
                        for evidence_id in claim["evidence_ids"]
                    ],
                }
            )
        return json.dumps({"answers": answers}, ensure_ascii=False)


class MalformedThenRepairedJSONLLM:
    def __init__(self, *, repair_succeeds=True):
        self.repair_succeeds = repair_succeeds
        self.trace_names = []

    @property
    def available(self):
        return True

    async def generate_text(self, *, trace_name, **kwargs):
        del kwargs
        self.trace_names.append(trace_name)
        if ".json_repair." in trace_name:
            if self.repair_succeeds:
                return '{"items":[{"id":"q1","value":"保留原业务值"}]}'
            return '{"items":[}'
        return '{"items":[{"id":"q1" "value":"保留原业务值"}]}'


class StopOnFirstRepairLLM:
    def __init__(self):
        self.calls = []

    @property
    def available(self):
        return True

    async def generate_text(self, *, trace_name, **kwargs):
        del kwargs
        self.calls.append(trace_name)
        raise RuntimeError("first repair batch stopped")


class OnePrunableClaimLLM(FakeAgenticInterviewLLM):
    def _answer_payload(self, items):
        payload = json.loads(super()._answer_payload(items))
        evidence_by_question = {
            item["question_id"]: item["evidence"][0]["evidence_id"]
            for item in items
        }
        for answer in payload["answers"]:
            answer["claims"].append(
                {
                    "text": "这条故意构造的额外说法会被独立 verifier 拒绝，但不应导致其余三条已支持事实被整题重写。",
                    "claim_type": "answer_strategy",
                    "evidence_ids": [evidence_by_question[answer["question_id"]]],
                }
            )
        return json.dumps(payload, ensure_ascii=False)

    async def generate_text(self, *, trace_name, **kwargs):
        raw = await super().generate_text(trace_name=trace_name, **kwargs)
        if not trace_name.startswith("interview_agentic_rag.verify."):
            return raw
        payload = json.loads(raw)
        rejected_questions = set()
        for verdict in payload["verdicts"]:
            question_id = verdict["question_id"]
            if question_id in rejected_questions or verdict["claim_index"] != 3:
                continue
            verdict.update(
                {
                    "supported": False,
                    "normalized_evidence_ids": [],
                    "reason": "测试故意拒绝额外 claim。",
                }
            )
            rejected_questions.add(question_id)
        return json.dumps(payload, ensure_ascii=False)


def _create_grounded_prep(db_session, *, profile, job, experience_ids=None):
    return asyncio.run(
        InterviewPrepService(llm=FakeAgenticInterviewLLM()).create_interview_prep_with_llm(
            db_session,
            profile=profile,
            job=job,
            experience_ids=experience_ids,
        )
    )


def test_interview_default_flow_uses_batched_verifier_within_context_budget(db_session):
    profile, job = _seed_profile_job(db_session)
    llm = FakeAgenticInterviewLLM()

    prep = asyncio.run(
        InterviewPrepService(llm=llm).create_interview_prep_with_llm(
            db_session,
            profile=profile,
            job=job,
        )
    )

    questions = [
        question
        for group in prep.question_sets_json
        for question in group.get("questions", [])
    ]
    assert len(questions) == 10
    assert len(llm.calls) == 5
    assert [item["trace_name"] for item in llm.calls] == [
        "interview_prep.generate_interviewer_questions",
        "interview_agentic_rag.generate.1",
        "interview_agentic_rag.generate.2",
        "interview_agentic_rag.verify.1",
        "interview_agentic_rag.verify.2",
    ]
    assert sum(item["prompt_chars"] for item in llm.calls) <= 60000
    assert sum(item["max_tokens"] for item in llm.calls) <= 18000
    verify_calls = [item for item in llm.calls if item["trace_name"].startswith("interview_agentic_rag.verify.")]
    assert len(verify_calls) == 2
    assert all(item["prompt_chars"] < 10000 for item in verify_calls)
    assert all(item["max_tokens"] == 1800 for item in verify_calls)


def test_interview_repair_batches_stop_sequentially_after_first_failure(db_session):
    profile, job = _seed_profile_job(db_session)
    llm = StopOnFirstRepairLLM()
    service = InterviewAgenticRAGService(llm=llm)
    questions = [
        {
            "question_id": f"q{index}",
            "question": f"问题 {index}",
            "intent": "修复不受支持的 claim",
            "skills": ["RAG"],
        }
        for index in range(1, 7)
    ]
    plans = {
        item["question_id"]: {"intent": item["intent"]}
        for item in questions
    }
    evidence = {
        item["question_id"]: [
            {
                "evidence_id": f"resume:{item['question_id']}",
                "source_type": "resume",
                "text": "候选人简历中的项目证据。",
            }
        ]
        for item in questions
    }
    answers = {
        item["question_id"]: {
            "question_id": item["question_id"],
            "claims": [],
        }
        for item in questions
    }
    errors = [
        {"question_id": item["question_id"], "code": "claim_not_supported", "message": "测试"}
        for item in questions
    ]

    with pytest.raises(RuntimeError, match="first repair batch stopped"):
        asyncio.run(
            service._repair_answers(
                db_session,
                profile=profile,
                job=job,
                questions=questions,
                plans=plans,
                evidence=evidence,
                answers=answers,
                errors=errors,
                repair_round=1,
            )
        )

    assert llm.calls == ["interview_agentic_rag.repair.round1.1"]


def test_interview_prunes_one_bad_claim_without_rewriting_complete_answers(db_session):
    profile, job = _seed_profile_job(db_session)
    llm = OnePrunableClaimLLM()

    prep = asyncio.run(
        InterviewPrepService(llm=llm).create_interview_prep_with_llm(
            db_session,
            profile=profile,
            job=job,
        )
    )

    questions = [
        question
        for group in prep.question_sets_json
        for question in group.get("questions", [])
    ]
    assert len(llm.calls) == 5
    assert not any("repair" in item["trace_name"] for item in llm.calls)
    assert prep.summary_json["agentic_rag"]["repair_attempts"] == 0
    assert prep.summary_json["agentic_rag"]["verification_warning_count"] == len(questions)
    assert all(len(question["claims"]) == 3 for question in questions)


def test_agentic_rag_repairs_malformed_json_with_traced_llm_node(db_session):
    llm = MalformedThenRepairedJSONLLM()
    service = InterviewAgenticRAGService(llm=llm)

    payload = asyncio.run(
        service._generate_json(
            db_session,
            system_prompt="生成测试 JSON",
            user_prompt="保留业务字段",
            trace_name="interview_agentic_rag.test_json",
            max_tokens=300,
        )
    )

    assert payload == {"items": [{"id": "q1", "value": "保留原业务值"}]}
    assert llm.trace_names == [
        "interview_agentic_rag.test_json",
        "interview_agentic_rag.test_json.json_repair.1",
    ]


def test_agentic_rag_rejects_json_that_remains_invalid_after_repair(db_session):
    llm = MalformedThenRepairedJSONLLM(repair_succeeds=False)
    service = InterviewAgenticRAGService(llm=llm)

    with pytest.raises(InterviewAgenticRAGError, match="invalid JSON after 1 repair attempt"):
        asyncio.run(
            service._generate_json(
                db_session,
                system_prompt="生成测试 JSON",
                user_prompt="保留业务字段",
                trace_name="interview_agentic_rag.test_json_failure",
                max_tokens=300,
            )
        )


def test_answer_repair_keeps_local_evidence_aliases_without_exposing_database_ids():
    service = InterviewAgenticRAGService(llm=FakeAgenticInterviewLLM())

    payload = service._answer_for_repair(
        {
            "question_id": "q1",
            "reference_answer": "示例回答",
            "answer_framework": [],
            "claims": [
                {
                    "text": "候选人实现了 RAG 检索",
                    "claim_type": "candidate_experience",
                    "evidence_ids": ["resume_chunk:987"],
                }
            ],
        },
        evidence=[{"evidence_id": "resume_chunk:987"}],
    )

    assert payload["verified_claims"][0]["evidence_ids"] == ["E1"]
    assert "resume_chunk:987" not in json.dumps(payload, ensure_ascii=False)


def test_claim_verifier_rebinds_generator_miscitation_to_supporting_evidence(db_session):
    class CitationLinkerLLM:
        @property
        def available(self):
            return True

        async def generate_text(self, *, user_prompt, trace_name, **kwargs):
            del kwargs
            assert trace_name.startswith("interview_agentic_rag.verify.")
            item = _verifier_claims(json.loads(user_prompt))[0]
            project = next(
                evidence
                for evidence in item["available_evidence"]
                if evidence["source_type"] == "project_document"
            )
            return json.dumps(
                {
                    "verdicts": [
                        {
                            "question_id": item["question_id"],
                            "claim_index": item["claim_index"],
                            "supported": True,
                            "normalized_claim_type": "project_implementation",
                            "normalized_evidence_ids": [project["evidence_id"]],
                            "reason": "项目文档直接描述该实现。",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    service = InterviewAgenticRAGService(llm=CitationLinkerLLM())
    answers, errors = asyncio.run(
        service._verify_claim_entailment(
            db_session,
            questions=[{"question_id": "q1", "question": "如何记录 Agent trace？"}],
            evidence={
                "q1": [
                    {
                        "evidence_id": "job_chunk:1",
                        "source_type": "job",
                        "allowed_claim_types": ["job_requirement", "job_responsibility"],
                        "text": "岗位关注 Agent evaluation。",
                    },
                    {
                        "evidence_id": "project_document:architecture:1",
                        "source_type": "project_document",
                        "allowed_claim_types": ["project_implementation", "technical_explanation"],
                        "text": "AgentStep 和 AgentArtifact 持久化执行轨迹与产物。",
                    },
                ]
            },
            answers={
                "q1": {
                    "question_id": "q1",
                    "reference_answer": "项目使用 AgentStep 和 AgentArtifact 记录轨迹。",
                    "answer_framework": [],
                    "claims": [
                        {
                            "text": "项目使用 AgentStep 和 AgentArtifact 记录轨迹。",
                            "claim_type": "project_implementation",
                            "evidence_ids": [],
                        }
                    ],
                    "citations": [],
                }
            },
        )
    )

    assert errors == []
    assert answers["q1"]["claims"][0]["evidence_ids"] == ["project_document:architecture:1"]
    assert answers["q1"]["citations"][0]["evidence_id"] == "project_document:architecture:1"


def test_claim_verifier_prunes_rejected_claims_before_repair(db_session):
    class RejectingVerifierLLM:
        @property
        def available(self):
            return True

        async def generate_text(self, *, user_prompt, **kwargs):
            del kwargs
            item = _verifier_claims(json.loads(user_prompt))[0]
            return json.dumps(
                {
                    "verdicts": [
                        {
                            "question_id": item["question_id"],
                            "claim_index": item["claim_index"],
                            "supported": False,
                            "normalized_claim_type": "project_implementation",
                            "normalized_evidence_ids": [],
                            "reason": "证据没有直接描述该实现。",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    answers, errors = asyncio.run(
        InterviewAgenticRAGService(llm=RejectingVerifierLLM())._verify_claim_entailment(
            db_session,
            questions=[{"question_id": "q1", "question": "项目是否实现了未知功能？"}],
            evidence={
                "q1": [
                    {
                        "evidence_id": "project_document:1",
                        "source_type": "project_document",
                        "allowed_claim_types": ["project_implementation", "technical_explanation"],
                        "text": "项目实现了可追踪的 RAG 检索。",
                    }
                ]
            },
            answers={
                "q1": {
                    "question_id": "q1",
                    "reference_answer": "项目实现了不存在的未知功能。",
                    "answer_framework": [],
                    "claims": [
                        {
                            "text": "项目实现了不存在的未知功能。",
                            "claim_type": "project_implementation",
                            "evidence_ids": ["project_document:1"],
                        }
                    ],
                    "citations": [],
                }
            },
        )
    )

    assert errors[0]["code"] == "claim_not_supported"
    assert answers["q1"]["claims"] == []
    assert answers["q1"]["citations"] == []


def test_claim_verifier_only_sends_cited_and_rebinding_evidence():
    service = InterviewAgenticRAGService(llm=FakeAgenticInterviewLLM())
    evidence = [
        {
            "evidence_id": "job:1",
            "source_type": "job",
            "allowed_claim_types": sorted(SOURCE_CLAIM_POLICY["job"]),
        },
        {
            "evidence_id": "resume:1",
            "source_type": "resume",
            "allowed_claim_types": sorted(SOURCE_CLAIM_POLICY["resume"]),
        },
        {
            "evidence_id": "project:1",
            "source_type": "project_document",
            "allowed_claim_types": sorted(SOURCE_CLAIM_POLICY["project_document"]),
        },
        {
            "evidence_id": "knowledge:1",
            "source_type": "technical_knowledge",
            "allowed_claim_types": sorted(SOURCE_CLAIM_POLICY["technical_knowledge"]),
        },
        {
            "evidence_id": "interview:1",
            "source_type": "interview_experience",
            "allowed_claim_types": sorted(SOURCE_CLAIM_POLICY["interview_experience"]),
        },
    ]

    selected = service._verification_evidence(
        evidence,
        [
            {
                "text": "项目实现了 trace。",
                "claim_type": "project_implementation",
                "evidence_ids": ["job:1"],
            }
        ],
    )

    assert selected == {"job:1", "resume:1", "project:1", "knowledge:1", "interview:1"}


def _seed_profile_job(db_session):
    profile = Profile(
        name="陈同学",
        headline="Agent 开发实习生候选人",
        target_roles_json=["Agent 开发实习生"],
        source_type="guided",
        raw_resume_text=(
            "构建 CareerAgent，使用 Python、FastAPI、SQLite、RAG 和 Agent Trace。"
            "实现 PDF chunk、岗位匹配评测和投递 Guardrail。没有 MLflow 生产经验。"
        ),
        structured_profile_json={
            "name": "陈同学",
            "headline": "Agent 开发实习生候选人",
            "target_roles": ["Agent 开发实习生"],
            "skills": ["Python", "FastAPI", "SQLite", "RAG", "Agent Trace"],
            "projects": [
                {
                    "name": "CareerAgent",
                    "description": "构建中文求职助手 Agent，支持 PDF chunk、RAG 检索、岗位匹配和投递 Guardrail。",
                    "tech_stack": ["Python", "FastAPI", "SQLite", "RAG"],
                    "impact": "用评测集跟踪 JD parser、RAG 和投递包质量。",
                }
            ],
            "raw_text": (
                "构建 CareerAgent，使用 Python、FastAPI、SQLite、RAG 和 Agent Trace。"
                "实现 PDF chunk、岗位匹配评测和投递 Guardrail。没有 MLflow 生产经验。"
            ),
        },
    )
    job = Job(
        source="manual",
        external_id="interview-agent-intern",
        title="Agent 开发实习生",
        company="腾讯",
        location="深圳",
        job_type="实习",
        raw_jd_text=(
            "负责 Agent 应用开发，使用 Python、FastAPI、RAG、SQLite 和评测体系，"
            "了解 MLflow 加分。需要参与需求拆解、效果评估和系统优化。"
        ),
        structured_jd_json={
            "title": "Agent 开发实习生",
            "company": "腾讯",
            "required_skills": ["Python", "FastAPI", "RAG", "SQLite", "MLflow"],
            "preferred_skills": ["Agent Trace", "Evaluation"],
            "responsibilities": ["开发 Agent 应用", "建设 RAG 检索与评测链路", "参与需求拆解和效果评估"],
            "keywords": ["Agent", "Guardrail", "PDF Chunk"],
        },
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)
    SQLiteVectorIndex().upsert_profile_chunks(
        db_session,
        profile.id,
        ResumeTextSplitter().build_resume_chunks(profile.structured_profile_json),
    )
    return profile, job


def test_interview_prep_without_imported_experience_focuses_on_project_and_jd(db_session):
    profile, job = _seed_profile_job(db_session)

    prep = _create_grounded_prep(db_session, profile=profile, job=job)

    categories = {group["category"] for group in prep.question_sets_json}
    questions = [question for group in prep.question_sets_json for question in group.get("questions", [])]
    preparation_angles = {item["angle"]: item for item in prep.summary_json["preparation_angles"]}
    assert "同岗位面经与高频追问" not in categories
    assert "LLM 项目实现追问" in categories
    assert "LLM 八股与基础追问" in categories
    assert len(questions) == 10
    assert set(preparation_angles) == {
        "same_role_interview_experience",
        "resume_project_tech_stack",
        "other_possible_interview_questions",
    }
    assert preparation_angles["same_role_interview_experience"]["question_count"] == 0
    assert preparation_angles["resume_project_tech_stack"]["question_count"] > 0
    assert preparation_angles["other_possible_interview_questions"]["question_count"] > 0
    assert {question["preparation_angle"] for question in questions} >= {
        "resume_project_tech_stack",
        "other_possible_interview_questions",
    }
    assert prep.coverage_json["passed"] is True
    assert prep.coverage_json["preparation_angles_passed"] is True
    assert prep.coverage_json["question_quality_passed"] is True
    assert prep.summary_json["question_quality"]["passed"] is True
    assert prep.summary_json["question_quality"]["score"] >= 0.82
    assert prep.coverage_json["required_skill_coverage_rate"] == 1.0
    assert prep.coverage_json["missing_skill_drill_rate"] == 1.0
    assert prep.summary_json["question_quality"]["rates"]["reference_answer_usability"] == 1.0
    assert prep.summary_json["question_quality"]["rates"]["citation_integrity"] == 1.0
    assert prep.summary_json["question_quality"]["rates"]["source_policy_coverage"] == 1.0
    assert prep.summary_json["agentic_rag"]["framework"] == "langgraph"
    assert all(len(str(question.get("reference_answer") or "")) >= 120 for question in questions)
    assert {item["site"] for item in prep.research_checklist_json} >= {"牛客网", "OfferShow", "小红书"}
    assert any(item["skill"] == "MLflow" for item in prep.gap_drills_json)
    assert "不能包装成已交付经验" in prep.summary_json["boundary"]
    assert all(len(question.get("follow_ups") or []) >= 2 for question in questions)


def test_interview_prep_quality_judge_flags_weak_questions(db_session):
    profile, job = _seed_profile_job(db_session)
    service = InterviewPrepService()
    weak_question_sets = [
        {
            "category": "弱题样例",
            "questions": [
                {
                    "question_id": "weak_1",
                    "question": "介绍一下你自己。",
                    "source_perspective": "general_interview",
                    "risk_level": "low",
                    "skills": [],
                    "answer_points": [],
                    "follow_ups": [],
                }
            ],
        }
    ]

    quality = service._question_quality_judge(
        profile=profile,
        job=job,
        question_sets=weak_question_sets,
        required=["Python", "FastAPI", "RAG", "SQLite"],
        preferred=["Agent Trace"],
        keywords=["Agent", "Guardrail"],
        missing=["MLflow"],
        evidence=[],
    )

    assert quality["passed"] is False
    assert quality["score"] < quality["thresholds"]["score"]
    assert quality["issue_counts"]["jd_alignment"] >= 1
    assert quality["issue_counts"]["follow_up_depth"] >= 1
    assert quality["issue_counts"]["reference_answer_usability"] >= 1


def test_interview_prep_dedupes_questions_before_quality_gate(db_session):
    profile, job = _seed_profile_job(db_session)
    duplicate_question = {
        "question": "CareerAgent 的 RAG 检索从 query 到 evidence 的链路怎么设计？",
        "follow_ups": ["Top20 为什么需要 reranker？", "SQLite 和向量库如何分工？"],
        "intent": "验证项目实现细节。",
        "answer_points": ["讲 chunk", "讲检索权重", "讲 trace"],
        "skills": ["RAG", "SQLite"],
        "risk_level": "low",
        "source_perspective": "llm_project_implementation",
    }

    prep = InterviewPrepService().create_interview_prep(
        db_session,
        profile=profile,
        job=job,
        llm_question_sets=[
            {"category": "LLM 重复题组 A", "questions": [dict(duplicate_question), dict(duplicate_question)]},
            {"category": "LLM 重复题组 B", "questions": [dict(duplicate_question)]},
        ],
    )

    normalized = [
        InterviewPrepService()._normalize_question_text(question["question"])
        for group in prep.question_sets_json
        for question in group.get("questions", [])
    ]
    assert len(normalized) == len(set(normalized))
    assert prep.coverage_json["question_quality_rates"]["duplicate_rate"] == 0.0
    assert prep.summary_json["agentic_rag"]["status"] == "not_run"


def test_interview_prep_agent_workflow_records_artifact(db_session):
    profile, job = _seed_profile_job(db_session)

    run = asyncio.run(
        AgentOrchestrator(interview_prep=InterviewPrepService(llm=FakeAgenticInterviewLLM())).run(
            db_session,
            AgentRunRequest(task_type="prepare_interview_for_job", profile_id=profile.id, job_id=job.id),
        )
    )

    assert run.status == "completed"
    assert run.output_json["interview_prep_id"] > 0
    assert run.output_json["coverage"]["passed"] is True
    assert run.output_json["summary"]["llm_question_generation"]["enabled"] is True
    assert run.output_json["execution_plan"]["skills"] == [
        "evidence_retrieval",
        "fit_assessment",
        "interview_preparation",
    ]


def test_interview_prep_with_llm_generates_project_and_foundation_followups(db_session):
    profile, job = _seed_profile_job(db_session)

    prep = asyncio.run(
        InterviewPrepService(llm=FakeAgenticInterviewLLM()).create_interview_prep_with_llm(
            db_session,
            profile=profile,
            job=job,
        )
    )
    questions = [question for group in prep.question_sets_json for question in group.get("questions", [])]
    sources = {question["source_perspective"] for question in questions}
    markdown = InterviewPrepDeliveryService().render_markdown(prep)

    assert prep.generation_mode == "langgraph_agentic_rag_v3_cost_guarded"
    assert prep.summary_json["llm_workflow_run_id"]
    assert prep.summary_json["llm_question_generation"]["enabled"] is True
    assert {"llm_project_implementation", "llm_foundation_drill"} <= sources
    assert any(question.get("follow_ups") for question in questions)
    assert prep.coverage_json["preparation_angle_counts"]["resume_project_tech_stack"] >= 2
    assert prep.coverage_json["preparation_angle_counts"]["other_possible_interview_questions"] >= 3
    assert "连续追问" in markdown
    assert "面经参考链接" in markdown


def test_interview_experience_import_extracts_questions_topics_and_credibility(db_session):
    raw_text = (
        "牛客网 腾讯 Agent 开发实习一面。"
        "一面：面试官问 RAG 召回率怎么评估？"
        "追问：如果 FastAPI 接口并发变高，你会怎么定位瓶颈？"
        "二面：问 SQLite 存储 trace 和向量检索元数据有什么边界？"
    )

    row = InterviewExperienceService().create_experience(
        db_session,
        source_site="nowcoder",
        source_url="https://www.nowcoder.com/discuss/123456789",
        title="腾讯 Agent 开发实习一面",
        company="腾讯",
        role_keyword="Agent 开发实习生",
        raw_text=raw_text,
    )

    assert row.source_site == "牛客网"
    assert len(row.extracted_questions_json) >= 3
    assert {"RAG", "FastAPI", "SQLite"} <= set(row.topics_json)
    assert row.credibility_json["score"] >= 0.75
    assert row.credibility_json["has_url"] is True


def test_interview_references_filter_placeholders_and_build_honest_search_entries():
    links = InterviewReferenceService.normalize_links(
        [
            {
                "kind": "confirmed_imported_interview_experience",
                "site": "牛客网",
                "title": "示例面经",
                "url": "https://www.nowcoder.com/discuss/example-agent-intern",
            },
            {
                "kind": "confirmed_imported_interview_experience",
                "site": "牛客网",
                "title": "真实导入面经",
                "url": "https://www.nowcoder.com/discuss/123456789",
            },
            {
                "kind": "search_reference_link",
                "site": "牛客网",
                "topic": "同岗位面经",
                "query": "site:nowcoder.com Agent 开发实习生 面经",
                "url": "https://www.baidu.com/s?wd=old",
            },
            {
                "kind": "search_reference_link",
                "site": "小红书",
                "topic": "候选人经验",
                "query": "小红书 Agent 开发实习生 面经",
            },
            {
                "kind": "search_reference_link",
                "site": "OfferShow",
                "topic": "薪资与流程",
                "query": "Agent 开发实习生 offer",
            },
        ]
    )

    assert len(links) == 4
    assert all("example-agent-intern" not in str(item.get("url")) for item in links)
    assert links[0]["reference_type"] == "source_article"
    assert any(item["url"].startswith("https://www.nowcoder.com/search/all?query=") for item in links)
    assert any(item["url"].startswith("https://www.xiaohongshu.com/search_result?keyword=") for item in links)
    assert any(item["url"] == "https://offershow.cn/" for item in links)
    assert {item["reference_type_label"] for item in links} >= {"原文", "搜索入口", "平台入口"}
    assert InterviewReferenceService.normalize_links(links) == links


def test_legacy_interview_links_are_normalized_in_api_and_markdown(db_session):
    profile, job = _seed_profile_job(db_session)
    prep = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=job)
    summary = dict(prep.summary_json or {})
    summary["interview_reference_links"] = [
        {
            "kind": "confirmed_imported_interview_experience",
            "site": "牛客网",
            "title": "旧示例面经",
            "url": "https://www.nowcoder.com/discuss/example-agent-intern",
        },
        {
            "kind": "search_reference_link",
            "site": "牛客网",
            "title": "牛客网：同岗位面经",
            "query": "site:nowcoder.com Agent 开发实习生 面经",
            "url": "https://www.baidu.com/s?wd=legacy",
        },
    ]
    prep.summary_json = summary

    response = _interview_prep_response(prep)
    response_links = response.summary_json["interview_reference_links"]
    markdown = InterviewPrepDeliveryService().render_markdown(prep)

    assert len(response_links) == 1
    assert response_links[0]["title"] == "搜索牛客网：同岗位面经"
    assert response_links[0]["url"].startswith("https://www.nowcoder.com/search/all?query=")
    assert "example-agent-intern" not in markdown
    assert "https://www.nowcoder.com/search/all?query=" in markdown


def test_interview_prep_uses_imported_source_backed_experience_questions(db_session):
    profile, job = _seed_profile_job(db_session)
    experience = InterviewExperienceService().create_experience(
        db_session,
        job=job,
        source_site="牛客网",
        source_url="https://www.nowcoder.com/discuss/source-backed-agent",
        title="腾讯 Agent 实习面经",
        raw_text=(
            "一面：面试官问 RAG 的 chunk 切分策略怎么选？"
            "追问：FastAPI 并发接口如何记录 trace？"
            "二面：SQLite 存 JD chunk 和向量元数据有什么边界？"
        ),
    )

    prep = _create_grounded_prep(
        db_session,
        profile=profile,
        job=job,
        experience_ids=[experience.id],
    )
    source_questions = [
        question
        for group in prep.question_sets_json
        for question in group.get("questions", [])
        if question.get("source_perspective") == "source_backed_interview_experience"
    ]

    assert prep.summary_json["interview_experience_source_count"] == 1
    assert prep.coverage_json["research_mode"] == "source_backed_and_checklist"
    assert prep.coverage_json["source_backed_question_count"] >= 2
    assert source_questions
    assert any(ref.get("source_label") == "牛客网" for question in source_questions for ref in question["evidence_refs"])
    first_question = source_questions[0]
    assert first_question["question_generation_source"] == "imported_interview_experience"
    assert first_question["answer_framework_source"] == "agentic_rag_llm"
    assert first_question["retrieval_plan"]["confidence"] >= 0.55
    assert any(ref.get("source_type") == "resume" for ref in first_question["evidence_refs"])
    assert any(ref.get("source_type") == "interview_experience" for ref in first_question["evidence_refs"])
    storage_question = next(question for question in source_questions if "SQLite 存 JD chunk" in question["question"])
    assert storage_question["reference_answer_source"] == "agentic_rag_llm"
    assert storage_question["citations"]
    assert storage_question["claims"]


def test_interview_prep_passes_auto_selected_experience_ids_into_rag(db_session):
    profile, job = _seed_profile_job(db_session)
    sibling_job = Job(
        source="manual",
        external_id="sibling-agent-interview-source",
        title=job.title,
        company=job.company,
        location=job.location,
        job_type="实习",
        raw_jd_text=job.raw_jd_text,
        structured_jd_json=job.structured_jd_json,
    )
    db_session.add(sibling_job)
    db_session.commit()
    db_session.refresh(sibling_job)
    InterviewExperienceService().create_experience(
        db_session,
        job=sibling_job,
        source_site="牛客网",
        source_url="https://www.nowcoder.com/discuss/scoped-agent-interview",
        title="腾讯 Agent 开发实习同岗面经",
        raw_text="一面追问 RAG chunk 策略，二面追问 FastAPI 并发与 trace。",
    )

    prep = _create_grounded_prep(db_session, profile=profile, job=job)
    source_questions = [
        question
        for group in prep.question_sets_json
        for question in group.get("questions", [])
        if question.get("source_perspective") == "source_backed_interview_experience"
    ]

    assert source_questions
    assert all(
        any(ref.get("source_type") == "interview_experience" for ref in question["evidence_refs"])
        for question in source_questions
    )


def test_interview_answers_use_llm_plan_and_hybrid_retrieval_instead_of_keyword_router(db_session):
    service = InterviewAnswerFrameworkService()
    assert not hasattr(service, "_question_kind")
    profile, job = _seed_profile_job(db_session)
    prep = _create_grounded_prep(db_session, profile=profile, job=job)
    question = prep.question_sets_json[0]["questions"][0]
    assert question["retrieval_plan"]["intent"]
    assert question["retrieval_plan"]["search_queries"]
    assert question["reference_answer_source"] == "agentic_rag_llm"
    assert question["evidence_refs"]
    retrieval_trace = question["evidence_refs"][0]["retrieval_trace"]
    assert set(retrieval_trace["channel_scores"]) == {"exact", "bm25", "vector"}
    assert retrieval_trace["rrf_k"] == 60


def test_legacy_answer_framework_is_upgraded_on_api_read(db_session):
    profile, job = _seed_profile_job(db_session)
    prep = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=job)
    question = prep.question_sets_json[0]["questions"][0]
    question["answer_points"] = [
        "先标注来源：牛客网 / 示例面经。",
        "围绕 Python、FastAPI、SQLite、RAG 说明可引用的项目、指标或缺口边界。",
        "不能包装成生产经验。",
    ]
    question["question"] = "导入面经提到：RAG 的 chunk 切分策略怎么选？"
    question.pop("answer_framework", None)
    question["source_perspective"] = "source_backed_interview_experience"
    question["skills"] = ["RAG"]
    question["evidence_refs"] = [
        {
            "ref": "interview_experience:99",
            "source_site": "牛客网",
            "source_url": "https://www.nowcoder.com/discuss/example-agent-intern",
            "preview": "RAG 的 chunk 切分策略怎么选？",
        }
    ]

    response = _interview_prep_response(prep)
    upgraded = response.question_sets_json[0]["questions"][0]

    assert upgraded["requires_regeneration"] is True
    assert upgraded["answer_framework"] == []
    assert upgraded["reference_answer_source"] == "legacy_requires_regeneration"
    assert upgraded["reference_answer"] == ""
    assert "source_url" not in upgraded["evidence_refs"][0]
    assert upgraded["evidence_refs"][0]["preview"] == "RAG 的 chunk 切分策略怎么选？"


def test_interview_prep_delivery_exports_markdown_and_tracks_practice(db_session):
    profile, job = _seed_profile_job(db_session)
    prep = _create_grounded_prep(db_session, profile=profile, job=job)
    delivery = InterviewPrepDeliveryService()
    questions = delivery.question_items(prep)

    assert questions
    assert all(item["question_id"] for item in questions)
    assert all(
        item["reference_answer_version"] == "interview_agentic_rag_v3_cost_guarded"
        for item in questions
    )
    assert all(len(item["reference_answer_basis"]) == 20 for item in questions)
    source_summary = delivery.source_perspective_summary(prep)
    assert source_summary["core_perspectives"]["online_experience"] == 0
    assert source_summary["core_perspectives"]["resume_project_stack"] > 0
    assert source_summary["core_perspectives"]["other_interview_questions"] > 0
    assert source_summary["preparation_angle_counts"].get("same_role_interview_experience", 0) == 0
    assert source_summary["preparation_angle_counts"]["resume_project_tech_stack"] > 0
    assert source_summary["preparation_angle_counts"]["other_possible_interview_questions"] > 0
    assert prep.summary_json["interview_reference_links"]
    assert all(item.get("url") for item in prep.summary_json["interview_reference_links"])

    first_question_id = questions[0]["question_id"]
    row = delivery.upsert_practice_item(
        db_session,
        prep,
        question_id=first_question_id,
        status="ready",
        confidence_score=4,
        notes="已按项目背景、行动、指标准备 90 秒回答。",
    )
    summary = delivery.progress_summary(prep, [row])
    markdown = delivery.render_markdown(prep, practice_items=[row])

    assert row.status == "ready"
    assert summary["ready_count"] == 1
    assert summary["ready_rate"] > 0
    assert first_question_id in markdown
    assert "状态：ready" in markdown
    assert "信心：4/5" in markdown
    assert "问题来源分布" in markdown
    assert "面经参考链接" in markdown
    assert "准备角度" in markdown
    assert "网上同岗位面经" in markdown
    assert "牛客网" in markdown
    assert "简历项目技术栈" in markdown
    assert "其他可能面试问题" in markdown
    assert "**参考回答**" in markdown
    assert "证据边界" in markdown

    updated = delivery.upsert_practice_item(
        db_session,
        prep,
        question_id=first_question_id,
        status="practicing",
        confidence_score=2,
        notes=None,
    )
    assert updated.notes == "已按项目背景、行动、指标准备 90 秒回答。"


def test_interview_prep_list_filters_by_profile_and_job(db_session):
    profile, job = _seed_profile_job(db_session)
    first = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=job)
    other_job = Job(
        source="manual",
        external_id="interview-agent-platform",
        title="Agent 平台开发实习生",
        company="阿里巴巴",
        location="杭州",
        job_type="实习",
        raw_jd_text="负责 Agent 平台、Python、RAG 与评测系统建设。",
        structured_jd_json={
            "title": "Agent 平台开发实习生",
            "company": "阿里巴巴",
            "required_skills": ["Python", "RAG", "Agent"],
            "responsibilities": ["建设 Agent 平台和评测系统"],
        },
    )
    db_session.add(other_job)
    db_session.commit()
    db_session.refresh(other_job)
    second = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=other_job)

    job_rows = list_interview_preps(profile_id=None, job_id=job.id, limit=50, db=db_session)
    profile_rows = list_interview_preps(profile_id=profile.id, job_id=None, limit=50, db=db_session)

    assert [row.id for row in job_rows] == [first.id]
    assert {row.id for row in profile_rows} == {first.id, second.id}


def test_interview_practice_rejects_unknown_question_id(db_session):
    profile, job = _seed_profile_job(db_session)
    prep = InterviewPrepService().create_interview_prep(db_session, profile=profile, job=job)

    with pytest.raises(ValueError, match="does not belong"):
        InterviewPrepDeliveryService().upsert_practice_item(
            db_session,
            prep,
            question_id="q99_99",
            status="ready",
            confidence_score=5,
        )


def test_interview_claim_verifier_rejects_jd_as_candidate_experience():
    service = InterviewAgenticRAGService(llm=FakeAgenticInterviewLLM())
    questions = [{"question_id": "q1", "question": "请结合项目说明 RAG 经验。"}]
    plans = {
        "q1": {
            "required_evidence": ["candidate_experience"],
            "target_sources": ["resume", "job"],
        }
    }
    evidence = {
        "q1": [
            {
                "evidence_id": "job_chunk:1",
                "source_type": "job",
                "allowed_claim_types": ["job_requirement", "job_responsibility", "answer_strategy"],
            }
        ]
    }
    answers = {
        "q1": {
            "question_id": "q1",
            "reference_answer": "我在项目中完成了 RAG 系统设计。" * 12,
            "answer_framework": [
                {"section": "结论", "guidance": "说明实现。"},
                {"section": "证据", "guidance": "引用项目。"},
                {"section": "边界", "guidance": "说明限制。"},
            ],
            "claims": [
                {
                    "text": "候选人完成了 RAG 系统设计",
                    "claim_type": "candidate_experience",
                    "evidence_ids": ["job_chunk:1"],
                }
            ],
            "citations": [{"evidence_id": "job_chunk:1", "claim": "候选人完成了 RAG 系统设计"}],
        }
    }

    errors = service._verify_answers(
        questions=questions,
        plans=plans,
        evidence=evidence,
        answers=answers,
    )

    assert any(item["code"] == "source_policy_violation" for item in errors)


def test_interview_first_stage_preserves_planned_sources_before_top20():
    service = InterviewAgenticRAGService(llm=FakeAgenticInterviewLLM())
    ranked = [
        {
            "evidence_id": f"project:{index}",
            "source_type": "project_document",
            "score": 1 - index / 100,
        }
        for index in range(30)
    ]
    ranked.extend(
        [
            {"evidence_id": "resume:1", "source_type": "resume", "score": 0.2},
            {"evidence_id": "job:1", "source_type": "job", "score": 0.1},
        ]
    )

    candidates = service._ensure_source_candidates(
        ranked,
        target_sources=["resume", "job", "project_document"],
        limit=20,
    )

    assert len(candidates) == 20
    assert {item["source_type"] for item in candidates} >= {"resume", "job", "project_document"}


def test_interview_project_plan_reserves_multiple_project_evidence_slots(db_session):
    service = InterviewAgenticRAGService(llm=FakeAgenticInterviewLLM())
    profile, job = _seed_profile_job(db_session)
    plans = service._build_retrieval_plans(
        profile=profile,
        job=job,
        questions=[
            {
                "question_id": "q-project",
                "question": "CareerAgent 的 RAG 链路如何实现？",
                "intent": "验证项目实现细节",
                "skills": ["RAG"],
                "source_perspective": "llm_project_implementation",
            }
        ],
        source_inventory={
            "resume": 4,
            "job": 3,
            "interview_experience": 2,
            "project_document": 4,
            "technical_knowledge": 4,
        },
    )
    plan = plans["q-project"]
    assert plan["target_sources"] == ["resume", "project_document", "job"]
    assert plan["source_quotas"] == {"resume": 2, "project_document": 2, "job": 1}

    ranked = [
        {
            "evidence_id": f"{source}:{index}",
            "source_type": source,
            "retrieval_rank": rank,
        }
        for rank, (source, index) in enumerate(
            [
                ("interview_experience", 1),
                ("technical_knowledge", 1),
                ("resume", 1),
                ("project_document", 1),
                ("job", 1),
                ("resume", 2),
                ("project_document", 2),
            ],
            start=1,
        )
    ]
    selected = service._source_diverse_top_k(
        ranked,
        target_sources=plan["target_sources"],
        source_quotas=plan["source_quotas"],
        top_k=5,
    )
    assert Counter(item["source_type"] for item in selected) == {
        "resume": 2,
        "project_document": 2,
        "job": 1,
    }


def test_interview_foundation_plan_prefers_two_resume_evidence_slots(db_session):
    service = InterviewAgenticRAGService(llm=FakeAgenticInterviewLLM())
    profile, job = _seed_profile_job(db_session)
    plans = service._build_retrieval_plans(
        profile=profile,
        job=job,
        questions=[
            {
                "question_id": "q-foundation",
                "question": "如何用 Python 构建 Agent 评测流程？",
                "intent": "结合 JD 和项目解释 Python 工程能力",
                "skills": ["Python", "Evaluation"],
                "source_perspective": "llm_foundation_drill",
            }
        ],
        source_inventory={
            "resume": 4,
            "job": 3,
            "project_document": 4,
            "technical_knowledge": 4,
        },
    )

    assert plans["q-foundation"]["target_sources"] == ["technical_knowledge", "job", "resume"]
    assert plans["q-foundation"]["source_quotas"] == {
        "technical_knowledge": 2,
        "job": 1,
        "resume": 2,
    }


def test_interview_agentic_rag_does_not_silently_fallback_without_llm(db_session):
    profile, job = _seed_profile_job(db_session)

    class UnavailableLLM:
        available = False

    with pytest.raises(InterviewAgenticRAGError, match="requires a configured LLM"):
        asyncio.run(
            InterviewPrepService(llm=UnavailableLLM()).create_interview_prep_with_llm(
                db_session,
                profile=profile,
                job=job,
            )
        )

    assert db_session.query(InterviewPrep).count() == 0


def test_interview_llm_configuration_fails_before_match_and_retrieval(db_session):
    profile, job = _seed_profile_job(db_session)

    class UnavailableLLM:
        available = False

    class MatcherMustNotRun:
        def create_match_result(self, *_args, **_kwargs):
            raise AssertionError("matcher must not run when LLM is unavailable")

    service = InterviewPrepService(llm=UnavailableLLM(), matcher=MatcherMustNotRun())
    service.settings = service.settings.model_copy(update={"llm_fallback_enabled": False})

    with pytest.raises(LLMConfigurationError, match="Configure LLM_API_KEY"):
        asyncio.run(
            service.create_interview_prep_with_llm(
                db_session,
                profile=profile,
                job=job,
            )
        )

    assert db_session.query(InterviewPrep).count() == 0


def test_interview_release_gate_failure_does_not_persist_partial_result(db_session):
    profile, job = _seed_profile_job(db_session)
    service = InterviewPrepService(llm=FakeAgenticInterviewLLM())
    original_judge = service._question_quality_judge

    def rejected_quality(**kwargs):
        result = original_judge(**kwargs)
        return {**result, "passed": False, "sample_issues": ["forced release gate failure"]}

    service._question_quality_judge = rejected_quality
    before_count = db_session.query(InterviewPrep).count()

    with pytest.raises(InterviewAgenticRAGError, match="release gate failed"):
        asyncio.run(
            service.create_interview_prep_with_llm(
                db_session,
                profile=profile,
                job=job,
            )
        )

    assert db_session.query(InterviewPrep).count() == before_count


def test_interview_retrieval_plan_uses_cost_free_multi_query_builder(db_session):
    service = InterviewAgenticRAGService(llm=FakeAgenticInterviewLLM())
    profile, job = _seed_profile_job(db_session)

    plans = service._build_retrieval_plans(
        profile=profile,
        job=job,
        questions=[
            {
                "question_id": "q1",
                "question": "RAG 为什么需要 reranker？",
                "intent": "说明检索和重排取舍",
                "skills": ["RAG", "Reranker"],
            }
        ],
        source_inventory={"resume": 3, "job": 2, "project_document": 4},
    )

    assert plans["q1"]["planner_mode"] == "multi_query_builder_no_llm"
    assert plans["q1"]["target_sources"] == ["resume", "job", "project_document"]
    assert len(plans["q1"]["search_queries"]) <= 3


def test_interview_langgraph_repairs_semantically_unsupported_claims(db_session):
    profile, job = _seed_profile_job(db_session)
    match = MatcherService().create_match_result(db_session, profile, job)

    class RepairingLLM(FakeAgenticInterviewLLM):
        reject_claims = True

        async def generate_text(self, **kwargs):
            trace_name = kwargs["trace_name"]
            if trace_name.startswith("interview_agentic_rag.verify."):
                payload = json.loads(kwargs["user_prompt"])
                claims = _verifier_claims(payload)
                return json.dumps(
                    {
                        "verdicts": [
                            {
                                "question_id": item["question_id"],
                                "claim_index": item["claim_index"],
                                "supported": not self.reject_claims,
                                "normalized_claim_type": (
                                    item["claim"].get("claim_type")
                                    if item["claim"].get("claim_type") in item["allowed_claim_types"]
                                    else item["allowed_claim_types"][0]
                                ),
                                "normalized_evidence_ids": item["current_evidence_ids"],
                                "reason": "首次校验故意模拟证据不蕴含，修复后通过。",
                            }
                            for item in claims
                        ]
                    },
                    ensure_ascii=False,
                )
            if trace_name.startswith("interview_agentic_rag.repair."):
                self.reject_claims = False
            return await super().generate_text(**kwargs)

    question_sets = [
        {
            "category": "RAG 深挖",
            "questions": [
                {
                    "question_id": "q01_01",
                    "question": "请结合 CareerAgent 说明 RAG 混合检索为什么需要 reranker。",
                    "follow_ups": ["RRF 如何融合？", "如何评测？"],
                    "intent": "检验 RAG 工程设计。",
                    "skills": ["RAG", "Reranker"],
                    "risk_level": "low",
                    "source_perspective": "resume_project_evidence",
                }
            ],
        }
    ]

    result = asyncio.run(
        InterviewAgenticRAGService(llm=RepairingLLM()).run(
            db_session,
            profile=profile,
            job=job,
            match_result=match,
            question_sets=question_sets,
        )
    )

    trace_nodes = [item["node"] for item in result["summary"]["graph_trace"]]
    assert result["summary"]["repair_attempts"] == 1
    assert trace_nodes.count("verify_claims") == 2
    assert "repair_answers" in trace_nodes
    assert result["question_sets"][0]["questions"][0]["reference_answer_source"] == "agentic_rag_llm"
