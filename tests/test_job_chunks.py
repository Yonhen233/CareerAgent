import asyncio

from app.models.entities import Job, Profile
from app.services.jd_parser import JDParserService
from app.services.text_splitter import PDFPageText, ResumeTextSplitter, TextChunk
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


def test_structured_and_pdf_views_share_fact_id_and_are_not_counted_twice():
    splitter = ResumeTextSplitter()
    structured = splitter.split_structured_profile(
        {
            "projects": [{
                "name": "CareerAgent",
                "description": "实现 LangGraph 工作流和 checkpoint 恢复",
                "tech_stack": ["LangGraph", "Redis"],
            }]
        }
    )
    pages = splitter.split_pdf_pages([
        PDFPageText(
            page_no=1,
            text="项目经历\nCareerAgent\n实现 LangGraph 工作流和 checkpoint 恢复，使用 Redis。",
        )
    ])
    linked = splitter.link_pdf_chunks_to_facts([*structured, *pages])

    project = next(item for item in linked if item.chunk_type == "project")
    source_view = next(item for item in linked if item.source == "profile.pdf_page_text")
    assert project.metadata["fact_id"] == "projects:0"
    assert source_view.metadata["fact_id"] == "projects:0"

    index = SQLiteVectorIndex()
    candidates = [
        RetrievedChunk(1, "structured", project.text, "project", project.source, 0.90, project.metadata),
        RetrievedChunk(2, "pdf", source_view.text, "raw_text", source_view.source, 0.80, source_view.metadata),
    ]
    deduped = index._deduplicate_fact_views(candidates, top_k=8)
    assert len(deduped) == 1
    assert deduped[0].metadata["evidence_view_count"] == 2
    assert source_view.text in deduped[0].text


def test_detailed_pdf_paragraph_links_to_short_project_summary_by_fact_anchors():
    splitter = ResumeTextSplitter()
    structured = splitter.split_structured_profile(
        {
            "projects": [{
                "name": "CareerAgent",
                "description": "使用 LangGraph 构建 Agent 工作流",
                "tech_stack": ["LangGraph", "Agent"],
            }]
        }
    )
    detail = TextChunk(
        uid="pdf_detail",
        text=(
            "将 LangGraph 流程拆分为意图识别、岗位检索和简历匹配节点，"
            "设计多个 Agent 协作，并通过 checkpoint 保存运行状态。"
        ),
        chunk_type="raw_text",
        source="profile.pdf_page_text",
        metadata={"page_no": 2},
    )

    linked = splitter.link_pdf_chunks_to_facts([*structured, detail])
    linked_detail = next(item for item in linked if item.uid == "pdf_detail")

    assert linked_detail.metadata["fact_id"] == "projects:0"
    assert linked_detail.metadata["evidence_scope"] == "fact_detail"
    assert linked_detail.metadata["fact_links"][0]["match_type"] == "semantic_detail"
    assert "langgraph" in linked_detail.metadata["fact_links"][0]["matched_anchors"]


def test_planned_pdf_paragraph_is_not_linked_as_delivered_project_detail():
    splitter = ResumeTextSplitter()
    structured = splitter.split_structured_profile(
        {"projects": [{"name": "CareerAgent", "description": "使用 LangGraph 构建 Agent 工作流"}]}
    )
    planned = TextChunk(
        uid="pdf_planned",
        text="未来计划学习 LangGraph，并考虑为 Agent 增加 checkpoint。",
        chunk_type="raw_text",
        source="profile.pdf_page_text",
        metadata={"page_no": 2},
    )

    linked = splitter.link_pdf_chunks_to_facts([*structured, planned])
    linked_planned = next(item for item in linked if item.uid == "pdf_planned")

    assert "fact_id" not in linked_planned.metadata
    assert "fact_links" not in linked_planned.metadata


def test_ambiguous_detail_keeps_multiple_fact_links_without_claiming_one_fact_id():
    splitter = ResumeTextSplitter()
    structured = splitter.split_structured_profile(
        {
            "projects": [
                {"name": "JobAgent", "description": "使用 LangGraph 构建 Agent 工作流"},
                {"name": "StudyAgent", "description": "使用 LangGraph 构建 Agent 学习助手"},
            ]
        }
    )
    ambiguous = TextChunk(
        uid="pdf_ambiguous",
        text="使用 LangGraph 设计多个 Agent 节点并实现状态编排。",
        chunk_type="raw_text",
        source="profile.pdf_page_text",
        metadata={"page_no": 2},
    )

    linked = splitter.link_pdf_chunks_to_facts([*structured, ambiguous])
    linked_ambiguous = next(item for item in linked if item.uid == "pdf_ambiguous")

    assert "fact_id" not in linked_ambiguous.metadata
    assert {item["fact_id"] for item in linked_ambiguous.metadata["fact_links"]} == {
        "projects:0",
        "projects:1",
    }
