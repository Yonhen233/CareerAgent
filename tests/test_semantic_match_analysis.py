import asyncio
import json

import pytest

from app.core.llm import LLMConfigurationError, LLMResponseError
from app.models.entities import Job, Profile
from app.services.semantic_match_analysis import SemanticMatchAnalysisService


class FakeGroundedLLM:
    available = True

    async def generate_text(self, **_kwargs):
        return json.dumps(
            {
                "fit_score": 82,
                "summary": "项目经历与岗位的 Agent 工程主线较一致。",
                "matched": [
                    {
                        "requirement": "Agent 工作流开发",
                        "jd_quote": "负责智能体工作流的设计与落地",
                        "resume_quote": "实现可恢复的 LangGraph 求职工作流",
                        "reason": "框架名称不同不影响能力语义匹配。",
                    }
                ],
                "gaps": [
                    {
                        "requirement": "高并发服务经验",
                        "jd_quote": "具备高并发服务设计经验",
                        "reason": "简历中没有可验证的并发规模证据。",
                    }
                ],
                "suggestions": ["补充吞吐量、并发数或压测结果。"],
            },
            ensure_ascii=False,
        )


class FakeUngroundedLLM(FakeGroundedLLM):
    async def generate_text(self, **_kwargs):
        payload = json.loads(await super().generate_text())
        payload["matched"][0]["resume_quote"] = "不存在的生产部署经历"
        payload["gaps"][0]["jd_quote"] = "不存在的 Kubernetes 要求"
        return json.dumps(payload, ensure_ascii=False)


class FakeAlternativeGapLLM(FakeGroundedLLM):
    async def generate_text(self, **_kwargs):
        payload = json.loads(await super().generate_text())
        payload["gaps"] = [
            {
                "requirement": "Java 编程能力",
                "jd_quote": "熟练掌握Python或Java",
                "reason": "候选人已有 Python，因此不应将 Java 判为缺口。",
            }
        ]
        return json.dumps(payload, ensure_ascii=False)


class UnavailableLLM:
    available = False


def _fixtures():
    profile = Profile(
        name="候选人",
        source_type="guided",
        raw_resume_text="项目：使用 Python 实现可恢复的 LangGraph 求职工作流。",
        structured_profile_json={"skills": ["Python"], "projects": [{"description": "使用 Python 实现可恢复的 LangGraph 求职工作流"}]},
    )
    job = Job(
        source="official",
        external_id="semantic-match",
        title="Agent 开发实习生",
        raw_jd_text="岗位职责：负责智能体工作流的设计与落地。任职要求：具备高并发服务设计经验，熟练掌握Python或Java。",
        structured_jd_json={
            "qualifications": ["具备高并发服务设计经验", "熟练掌握Python或Java"],
            "alternative_skill_groups": [
                {"label": "Python 或 Java", "skills": ["Python", "Java"], "min_required": 1}
            ],
        },
    )
    baseline = {
        "overall_score": 55.0,
        "dimension_scores": {"semantic_similarity": 60.0},
        "matched_skills": ["LangGraph"],
        "missing_skills": [],
        "suggestions": [],
        "relevant_evidence": [{"text": "实现可恢复的 LangGraph 求职工作流"}],
    }
    return profile, job, baseline


def test_semantic_match_accepts_only_cited_conclusions(db_session):
    profile, job, baseline = _fixtures()
    result = asyncio.run(
        SemanticMatchAnalysisService(FakeGroundedLLM()).analyze(
            db_session,
            profile=profile,
            job=job,
            baseline=baseline,
        )
    )

    assert result.applied is True
    assert result.payload["overall_score"] == 82
    assert result.payload["matched_skills"] == ["Agent 工作流开发"]
    assert result.payload["missing_skills"] == ["高并发服务经验"]
    assert result.metadata["citation_grounding_rate"] == 1.0


def test_semantic_match_rejects_uncited_model_conclusions(db_session):
    profile, job, baseline = _fixtures()
    with pytest.raises(LLMResponseError, match="引用完整性门禁"):
        asyncio.run(
            SemanticMatchAnalysisService(FakeUngroundedLLM()).analyze(
                db_session,
                profile=profile,
                job=job,
                baseline=baseline,
            )
        )


def test_semantic_match_rejects_gap_for_already_satisfied_alternative(db_session):
    profile, job, baseline = _fixtures()
    result = asyncio.run(
        SemanticMatchAnalysisService(FakeAlternativeGapLLM()).analyze(
            db_session,
            profile=profile,
            job=job,
            baseline=baseline,
        )
    )

    assert result.applied is True
    assert result.payload["missing_skills"] == []


def test_semantic_match_requires_llm_instead_of_returning_local_baseline(db_session):
    profile, job, baseline = _fixtures()
    with pytest.raises(LLMConfigurationError, match="需要可用的 LLM"):
        asyncio.run(
            SemanticMatchAnalysisService(UnavailableLLM()).analyze(
                db_session,
                profile=profile,
                job=job,
                baseline=baseline,
            )
        )
