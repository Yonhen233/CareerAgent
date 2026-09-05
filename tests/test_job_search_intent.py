import asyncio

from app.models.entities import Profile
from app.services.job_search_intent import JobSearchIntentService


class UnavailableLLM:
    available = False


class PlannedLLM:
    available = True

    async def generate_text(self, **_kwargs):
        return (
            '{"retrieval_query":"Agent 平台与 RAG 工程实习",'
            '"query_variants":["Agent 平台工程","RAG 检索评测"],'
            '"locations":[{"value":"粤港澳大湾区","evidence":"只考虑粤港澳大湾区"}],'
            '"excluded_terms":[{"value":"纯销售","evidence":"不要纯销售"},'
            '{"value":"前端","evidence":"用户没有说过这句话"}]}'
        )


def test_profile_only_intent_uses_delivery_evidence_without_treating_residence_as_constraint(db_session):
    profile = Profile(
        name="候选人",
        source_type="guided",
        raw_resume_text="构建知识助手。",
        target_roles_json=[],
        structured_profile_json={
            "location": "深圳",
            "skills": ["Python", "FastAPI", "RAG"],
            "projects": [{"name": "知识助手", "description": "实现混合检索、重排和引用评测"}],
        },
    )
    db_session.add(profile)
    db_session.commit()

    intent = asyncio.run(
        JobSearchIntentService(llm=UnavailableLLM()).plan(
            db_session, preference="", profile=profile, explicit_location=None
        )
    )

    assert "混合检索" in intent.retrieval_query
    assert intent.locations == []
    assert intent.profile_inference_used is True
    assert len(intent.query_variants) >= 2


def test_llm_intent_requires_verbatim_evidence_for_natural_language_constraints(db_session):
    intent = asyncio.run(
        JobSearchIntentService(llm=PlannedLLM()).plan(
            db_session,
            preference="想找 Agent 方向，工作地点只考虑粤港澳大湾区，不要纯销售",
            profile=None,
            explicit_location=None,
        )
    )

    assert intent.planner_mode == "llm_grounded"
    assert intent.locations == ["粤港澳大湾区"]
    assert intent.excluded_terms == ["纯销售"]
    assert "前端" not in intent.excluded_terms


def test_explicit_location_remains_authoritative_when_llm_is_unavailable(db_session):
    intent = asyncio.run(
        JobSearchIntentService(llm=UnavailableLLM()).plan(
            db_session,
            preference="找一个适合我的岗位",
            profile=None,
            explicit_location="深圳 / 远程",
        )
    )

    assert intent.locations == ["深圳", "远程"]
    assert intent.retrieval_query == "找一个适合我的岗位"
