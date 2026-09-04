import asyncio

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.main import app
from app.models.entities import Job, JobChunk, JobSearchResult, JobSearchSession, MatchResult, Profile
from app.models.schemas import JobDiscoveryRequest
from app.services.job_discovery import DiscoveryCandidate, JobDiscoveryService
from app.services.job_search import JobSearchService
from app.services.job_sources import JobPosting
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


def _job(db_session, *, external_id: str, title: str, location: str, skills: list[str]) -> Job:
    job = Job(
        source="manual",
        external_id=external_id,
        title=title,
        company="测试公司",
        location=location,
        job_type="实习",
        apply_url=None,
        raw_jd_text=(
            f"岗位：{title}\n岗位职责：参与 Agent 与 RAG 系统开发。\n"
            f"任职要求：熟悉{'、'.join(skills)}。\n招聘类型：实习"
        ),
        structured_jd_json={
            "required_skills": skills,
            "responsibilities": ["参与 Agent 与 RAG 系统开发"],
            "qualifications": [f"熟悉{'、'.join(skills)}"],
            "keywords": ["Agent", "RAG", *skills],
        },
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    chunks = ResumeTextSplitter().split_jd_text(
        job.raw_jd_text,
        job.structured_jd_json,
        prefix=f"job_{job.id}",
    )
    SQLiteVectorIndex().upsert_job_chunks(db_session, job.id, chunks)
    return job


class NoLiveSearch:
    async def search(self, *_args, **_kwargs):
        raise AssertionError("corpus mode must not call external job sources")


class FakeMatcher:
    def create_match_result(self, db, profile, job):
        required = job.structured_jd_json.get("required_skills") or []
        profile_skills = set(profile.structured_profile_json.get("skills") or [])
        matched = [skill for skill in required if skill in profile_skills]
        missing = [skill for skill in required if skill not in profile_skills]
        score = round(len(matched) / max(len(required), 1) * 100, 2)
        result = MatchResult(
            profile_id=profile.id,
            job_id=job.id,
            overall_score=score,
            dimension_scores_json={"required_skill_coverage": score},
            matched_skills_json=matched,
            missing_skills_json=missing,
            relevant_evidence_json=[{"text": profile.raw_resume_text}],
            suggestions_json=[f"补充 {skill}" for skill in missing],
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        return result


def test_preference_only_search_does_not_require_profile(db_session):
    agent_job = _job(
        db_session,
        external_id="agent-shenzhen",
        title="Agent 开发实习生",
        location="深圳",
        skills=["Python", "RAG", "FastAPI"],
    )
    _job(
        db_session,
        external_id="frontend-shenzhen",
        title="前端开发实习生",
        location="深圳",
        skills=["JavaScript", "Vue"],
    )

    session = asyncio.run(
        JobDiscoveryService(job_search=NoLiveSearch()).discover(
            db_session,
            JobDiscoveryRequest(
                preference_text="深圳 Agent RAG 开发实习",
                location="深圳",
                source_mode="corpus",
                limit=10,
            ),
        )
    )

    assert session.status == "completed"
    assert session.input_mode == "preference_only"
    assert session.profile_id is None
    assert session.result_count == 2
    assert session.results[0].job_id == agent_job.id
    assert session.results[0].match_score is None
    assert session.retrieval_quality_json["passed"] is True
    assert session.retrieval_quality_json["top_vector_score"] > 0
    assert db_session.query(JobSearchSession).count() == 1
    assert db_session.query(JobSearchResult).count() == 2


def test_user_discovery_excludes_evaluation_jobs_and_deduplicates_postings(db_session):
    first = _job(
        db_session,
        external_id="real-agent-role-1",
        title="Agent 开发实习生",
        location="深圳",
        skills=["Python", "RAG"],
    )
    duplicate = _job(
        db_session,
        external_id="real-agent-role-2",
        title="Agent 开发实习生",
        location="深圳",
        skills=["Python", "RAG"],
    )
    fixture = _job(
        db_session,
        external_id="evaluation-only",
        title="Agent 评测样本岗位",
        location="深圳",
        skills=["Python", "RAG"],
    )
    fixture.source = "llm_eval"
    db_session.commit()

    session = asyncio.run(
        JobDiscoveryService(job_search=NoLiveSearch()).discover(
            db_session,
            JobDiscoveryRequest(
                preference_text="深圳 Agent RAG 实习",
                location="深圳",
                source_mode="corpus",
                limit=10,
            ),
        )
    )

    assert session.result_count == 1
    assert session.results[0].job_id in {first.id, duplicate.id}
    assert session.results[0].job.source != "llm_eval"


def test_profile_only_search_derives_query_and_adds_match_evidence(db_session):
    profile = Profile(
        name="李明",
        headline="Agent 开发实习生",
        target_roles_json=["Agent 开发实习生"],
        source_type="guided",
        raw_resume_text="使用 Python、FastAPI 和 RAG 构建 CareerAgent。",
        structured_profile_json={
            "name": "李明",
            "target_roles": ["Agent 开发实习生"],
            "skills": ["Python", "FastAPI", "RAG"],
            "projects": [{"name": "CareerAgent", "description": "RAG 求职助手"}],
        },
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    expected = _job(
        db_session,
        external_id="agent-beijing",
        title="Agent 应用开发实习生",
        location="北京",
        skills=["Python", "FastAPI", "RAG"],
    )

    session = asyncio.run(
        JobDiscoveryService(job_search=NoLiveSearch(), matcher=FakeMatcher()).discover(
            db_session,
            JobDiscoveryRequest(profile_id=profile.id, source_mode="corpus", limit=10),
        )
    )

    assert session.input_mode == "profile_only"
    assert "Agent 开发实习生" in session.resolved_query
    assert "Python" in session.resolved_query
    assert session.results[0].job_id == expected.id
    assert session.results[0].match_score == 100
    assert session.results[0].match_result_id is not None
    assert session.results[0].reason_json["matched_skills"] == ["Python", "FastAPI", "RAG"]


def test_explicit_preference_is_kept_when_profile_is_also_provided(db_session):
    profile = Profile(
        name="李明",
        target_roles_json=["Agent 开发实习生"],
        source_type="guided",
        raw_resume_text="Python Agent 项目",
        structured_profile_json={
            "name": "李明",
            "location": "深圳",
            "target_roles": ["Agent 开发实习生"],
            "skills": ["Python", "LangGraph"],
        },
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    beijing = _job(
        db_session,
        external_id="agent-beijing-explicit",
        title="Agent 平台实习生",
        location="北京",
        skills=["Python", "LangGraph"],
    )
    _job(
        db_session,
        external_id="agent-shenzhen-profile",
        title="Agent 平台实习生",
        location="深圳",
        skills=["Python", "LangGraph"],
    )

    session = asyncio.run(
        JobDiscoveryService(job_search=NoLiveSearch(), matcher=FakeMatcher()).discover(
            db_session,
            JobDiscoveryRequest(
                preference_text="只看北京的 Agent 平台实习",
                profile_id=profile.id,
                location="北京",
                source_mode="corpus",
                limit=10,
            ),
        )
    )

    assert session.input_mode == "preference_and_profile"
    assert session.location == "北京"
    assert session.resolved_query == "只看北京的 Agent 平台实习"
    assert "LangGraph" not in session.resolved_query
    assert [result.job_id for result in session.results] == [beijing.id]


def test_preference_location_overrides_profile_location_when_field_is_empty(db_session):
    profile = Profile(
        name="李明",
        target_roles_json=["Agent 开发实习生"],
        source_type="guided",
        raw_resume_text="Python Agent 项目",
        structured_profile_json={"location": "深圳", "skills": ["Python", "Agent"]},
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    beijing = _job(
        db_session,
        external_id="preference-location-beijing",
        title="Agent 开发实习生",
        location="北京",
        skills=["Python", "Agent"],
    )
    _job(
        db_session,
        external_id="preference-location-shenzhen",
        title="Agent 开发实习生",
        location="深圳",
        skills=["Python", "Agent"],
    )

    session = asyncio.run(
        JobDiscoveryService(job_search=NoLiveSearch(), matcher=FakeMatcher()).discover(
            db_session,
            JobDiscoveryRequest(
                preference_text="只看北京或远程的 Agent 开发实习",
                profile_id=profile.id,
                source_mode="corpus",
                limit=10,
            ),
        )
    )

    assert session.location == "北京 / 远程"
    assert [result.job_id for result in session.results] == [beijing.id]


def test_sparse_jd_match_score_cannot_override_stronger_query_relevance(db_session):
    profile = Profile(
        name="李明",
        source_type="guided",
        raw_resume_text="Agent Python 项目",
        structured_profile_json={"skills": ["Agent", "Python"]},
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    sparse = _job(
        db_session,
        external_id="sparse-match-signal",
        title="Agent 操作系统工程师",
        location="北京",
        skills=["Agent"],
    )
    relevant = _job(
        db_session,
        external_id="strong-retrieval-signal",
        title="Agent RAG 后端开发实习生",
        location="北京",
        skills=["Agent", "Python", "RAG", "LangGraph"],
    )
    service = JobDiscoveryService(job_search=NoLiveSearch(), matcher=FakeMatcher())
    candidates = [
        DiscoveryCandidate(
            job=sparse,
            retrieval_score=65.0,
            rule_score=0.0,
            semantic_score=0.0,
            reasons=[],
            rerank={},
        ),
        DiscoveryCandidate(
            job=relevant,
            retrieval_score=80.0,
            rule_score=0.0,
            semantic_score=0.0,
            reasons=[],
            rerank={},
        ),
    ]

    service._attach_matches(db_session, profile, candidates)

    assert candidates[0].job.id == relevant.id
    assert candidates[1].match_signal_confidence == 0.25


def test_discovery_session_api_can_be_restored_after_page_refresh(db_session):
    job = _job(
        db_session,
        external_id="session-restore",
        title="Agent RAG 开发实习生",
        location="上海",
        skills=["Python", "RAG"],
    )
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        client = TestClient(app)
        created = client.post(
            "/job-discovery/sessions",
            json={
                "preference_text": "上海 Agent RAG 实习",
                "source_mode": "corpus",
                "limit": 10,
            },
        )
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["id"]
        restored = client.get(f"/job-discovery/sessions/{session_id}")
        assert restored.status_code == 200
        body = restored.json()
        assert body["session"]["status"] == "completed"
        assert body["session"]["input_mode"] == "preference_only"
        assert body["results"][0]["job"]["id"] == job.id
        assert body["results"][0]["match_score"] is None
    finally:
        app.dependency_overrides.clear()


def test_discovery_limits_expensive_vector_stage_to_bounded_job_pool(db_session, monkeypatch):
    for index in range(180):
        db_session.add(
            Job(
                source="test",
                external_id=f"pool-{index}",
                title=f"Agent 平台开发实习生 {index}",
                company=f"公司 {index}",
                location="上海",
                raw_jd_text="负责 Python、RAG、Agent 工具调用与评测。",
                structured_jd_json={
                    "required_skills": ["Python", "RAG", "Agent"],
                    "responsibilities": ["Agent 平台开发"],
                },
            )
        )
    db_session.commit()

    captured_job_ids: set[int] = set()
    service = JobDiscoveryService()

    def fake_query_job_corpus(_db, _query, *, job_ids, top_k, rerank):
        captured_job_ids.update(job_ids)
        assert top_k == 40
        assert rerank is False
        return []

    monkeypatch.setattr(service.vector_index, "query_job_corpus", fake_query_job_corpus)
    monkeypatch.setattr(
        service.reranker,
        "rerank_dicts",
        lambda _query, candidates, *, top_k: candidates[:top_k],
    )

    results = service._retrieve_candidates(
        db_session,
        query="Agent RAG 实习",
        location="上海",
        internship_only=True,
        tenant_id=None,
        limit=10,
    )

    assert results
    assert len(captured_job_ids) == 40


def test_rule_score_normalization_preserves_relevance_differences(db_session, monkeypatch):
    strong = _job(
        db_session,
        external_id="rule-normalization-strong",
        title="Agent RAG 后端开发实习生",
        location="北京",
        skills=["Python", "RAG", "LangGraph"],
    )
    weak = _job(
        db_session,
        external_id="rule-normalization-weak",
        title="大模型算法研究实习生",
        location="北京",
        skills=["Python", "PyTorch"],
    )
    service = JobDiscoveryService()
    monkeypatch.setattr(service.vector_index, "query_job_corpus", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        service.reranker,
        "rerank_dicts",
        lambda _query, candidates, *, top_k: candidates[:top_k],
    )

    candidates, _ = service._retrieve_candidates(
        db_session,
        query="北京 Agent RAG 后端开发实习",
        location="北京",
        internship_only=True,
        tenant_id=None,
        limit=10,
    )

    scores = {candidate.job.id: candidate.retrieval_score for candidate in candidates}
    assert scores[strong.id] > scores[weak.id]
    assert scores[strong.id] < 100


def test_candidate_quality_floor_removes_tail_noise(db_session):
    relevant = _job(
        db_session,
        external_id="candidate-floor-agent",
        title="Agent RAG 后端开发实习生",
        location="北京",
        skills=["Python", "RAG", "LangGraph"],
    )
    noise = _job(
        db_session,
        external_id="candidate-floor-autonomous-driving",
        title="自动驾驶决策规划算法实习生",
        location="北京",
        skills=["Python", "PyTorch"],
    )
    noise.raw_jd_text = "负责自动驾驶决策规划、控制算法和仿真评测。要求熟悉 Python、PyTorch。"
    noise.structured_jd_json = {
        "required_skills": ["Python", "PyTorch"],
        "responsibilities": ["自动驾驶决策规划与控制"],
        "qualifications": ["熟悉 Python 和 PyTorch"],
    }
    db_session.add(noise)
    db_session.commit()
    SQLiteVectorIndex().upsert_job_chunks(
        db_session,
        noise.id,
        ResumeTextSplitter().split_jd_text(
            noise.raw_jd_text,
            noise.structured_jd_json,
            prefix=f"job_{noise.id}",
        ),
    )

    session = asyncio.run(
        JobDiscoveryService(job_search=NoLiveSearch()).discover(
            db_session,
            JobDiscoveryRequest(
                preference_text="北京 Agent RAG 后端开发实习",
                location="北京",
                source_mode="corpus",
                limit=10,
            ),
        )
    )

    result_ids = [result.job_id for result in session.results]
    assert relevant.id in result_ids
    assert noise.id not in result_ids
    assert session.retrieval_quality_json["query_strategy"]["weak_candidate_count"] >= 1


def test_live_search_ingest_uses_fast_structured_parser_without_llm(monkeypatch):
    service = JobSearchService()
    posting = JobPosting(
        source="tencent",
        external_id="real-fast-parse",
        title="AI Agent 工程师",
        company="腾讯",
        location="深圳",
        job_type="全职",
        apply_url="https://careers.tencent.com/jobdesc.html?postId=real-fast-parse",
        raw_jd_text="岗位职责：开发 AI Agent 工作流。任职要求：熟悉 Python、RAG 和工具调用。",
        payload={},
    )

    async def fail_if_llm_parser_is_called(*_args, **_kwargs):
        raise AssertionError("search ingestion must not wait for the LLM parser")

    monkeypatch.setattr(service.jd_parser, "parse_jd", fail_if_llm_parser_is_called)
    parsed = asyncio.run(service._parse_postings_concurrently([posting]))

    assert len(parsed) == 1
    assert parsed[0][1]["title"] == "AI Agent 工程师"
    assert "Python" in parsed[0][1]["required_skills"]
    assert parsed[0][1]["prompt_injection"]["detected"] is False


def test_job_corpus_query_persists_recomputed_legacy_embeddings(db_session):
    job = _job(
        db_session,
        external_id="legacy-vector",
        title="Agent RAG 实习生",
        location="深圳",
        skills=["Python", "RAG"],
    )
    chunk = JobChunk(
        job_id=job.id,
        chunk_uid="legacy-vector-chunk",
        chunk_type="required_skills",
        source="jd",
        text="Python RAG Agent",
        token_count=3,
        embedding_json=[1.0],
        metadata_json={"embedding": {"provider": "legacy", "dimensions": 1}},
    )
    db_session.add(chunk)
    db_session.commit()
    db_session.refresh(chunk)

    index = SQLiteVectorIndex()
    results = index.query_job_corpus(
        db_session,
        "Python RAG",
        job_ids={job.id},
        top_k=5,
        rerank=False,
    )
    db_session.refresh(chunk)

    assert results
    assert len(chunk.embedding_json) == index.dimensions
    assert chunk.metadata_json["embedding"]["provider"] != "legacy"
