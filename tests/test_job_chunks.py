import asyncio

from app.models.entities import Job, Profile
from app.services.jd_parser import JDParserService
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import RetrievedChunk, SQLiteVectorIndex


def test_job_jd_chunks_are_stored_and_retrievable(db_session):
    jd_text = "Build Agent systems with Python, FastAPI, RAG, SQLite, evaluation and guardrails."
    structured = asyncio.run(
        JDParserService().parse_jd(
            jd_text,
            title="Agent Development Intern",
            company="Demo AI",
        )
    )
    job = Job(
        source="manual",
        external_id="job-chunk-test",
        title="Agent Development Intern",
        company="Demo AI",
        raw_jd_text=jd_text,
        structured_jd_json=structured,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    splitter = ResumeTextSplitter(chunk_size=120, chunk_overlap=20)
    chunks = splitter.split_jd_text(job.raw_jd_text, job.structured_jd_json, prefix=f"job_{job.id}")
    index = SQLiteVectorIndex()
    inserted = index.upsert_job_chunks(db_session, job.id, chunks)
    hits = index.query_job_chunks(db_session, job.id, "FastAPI RAG Agent", top_k=3)

    assert inserted >= 2
    assert hits
    assert any("FastAPI" in hit.text or "RAG" in hit.text for hit in hits)
    assert all(hit.metadata is not None for hit in hits)


def test_multi_query_uses_best_per_query_score_and_reranks_with_all_variants(db_session, monkeypatch):
    profile = Profile(
        name="候选人",
        source_type="guided",
        raw_resume_text="RAG project",
        target_roles_json=[],
        structured_profile_json={},
    )
    db_session.add(profile)
    db_session.commit()
    index = SQLiteVectorIndex()
    monkeypatch.setattr(index.settings, "rag_multi_query_enabled", True)
    monkeypatch.setattr(index.settings, "reranker_enabled", True)

    def fake_query_rows(*, query_text, **_kwargs):
        score = 0.2 if query_text == "岗位整体" else 0.9
        return [
            RetrievedChunk(
                chunk_id=1,
                chunk_uid="same-evidence",
                text="实现混合检索与重排",
                chunk_type="project",
                source="profile.projects",
                score=score,
                metadata={"retrieval": {"first_stage_score": score}},
            )
        ]

    captured = {}

    def fake_rerank(query, candidates, *, top_k):
        captured["query"] = query
        captured["score"] = candidates[0].score
        return candidates[:top_k]

    monkeypatch.setattr(index, "_query_rows", fake_query_rows)
    monkeypatch.setattr(index.reranker, "rerank_chunks", fake_rerank)
    results = index.query_profile_chunks_multi(
        db_session,
        profile.id,
        ["岗位整体", "RAG 检索评测"],
        top_k=1,
    )

    assert results
    assert captured["score"] > 0.7
    assert "岗位整体" in captured["query"]
    assert "RAG 检索评测" in captured["query"]
