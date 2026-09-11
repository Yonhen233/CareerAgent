from app.models.entities import Profile, ResumeChunk
from app.services.text_splitter import ResumeTextSplitter, TextChunk
from app.services.vector_index import PROFILE_INDEX_VERSION, RetrievedChunk, SQLiteVectorIndex


def test_sqlite_vector_index_retrieves_relevant_project(db_session):
    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Built an Agent workflow with RAG and FastAPI.",
        structured_profile_json={
            "skills": ["FastAPI", "RAG", "Agent"],
            "projects": [
                {
                    "name": "CareerAgent",
                    "description": "Agent job assistant with PDF chunking and SQLite RAG.",
                    "tech_stack": ["FastAPI", "SQLite"],
                    "impact": "End-to-end workflow",
                }
            ],
            "raw_text": "Built an Agent workflow with RAG and FastAPI.",
        },
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    chunks = ResumeTextSplitter().build_resume_chunks(profile.structured_profile_json)
    index = SQLiteVectorIndex()
    index.upsert_profile_chunks(db_session, profile.id, chunks)
    hits = index.query_profile_chunks(db_session, profile.id, "Agent RAG FastAPI", top_k=3)

    assert hits
    assert any(hit.chunk_type in {"project", "raw_text", "skill"} for hit in hits)

    filtered = index.query_profile_chunks_multi(
        db_session,
        profile.id,
        ["Agent RAG", "FastAPI SQLite"],
        top_k=5,
        allowed_chunk_types={"project", "skill"},
    )
    assert filtered
    assert all(hit.chunk_type in {"project", "skill"} for hit in filtered)


def test_resume_evidence_quality_prior_demotes_explicit_non_delivery():
    index = SQLiteVectorIndex()
    candidates = [
        RetrievedChunk(
            chunk_id=1,
            chunk_uid="planned",
            text="Planned learning: wants to study RAG next semester, no implementation yet.",
            chunk_type="education",
            source="profile.education",
            score=0.72,
            metadata={},
        ),
        RetrievedChunk(
            chunk_id=2,
            chunk_uid="delivered",
            text="Built and shipped a RAG service with evaluation metrics.",
            chunk_type="project",
            source="profile.projects",
            score=0.64,
            metadata={},
        ),
    ]

    ranked = index._apply_evidence_quality_prior(candidates)

    assert [item.chunk_uid for item in ranked] == ["delivered", "planned"]
    assert ranked[0].metadata["retrieval"]["evidence_quality_prior"] > 0
    assert ranked[1].metadata["retrieval"]["evidence_quality_prior"] < 0


def test_row_vectors_rebuild_when_embedding_model_changes_even_if_dimensions_match():
    class FakeEmbedding:
        def embed_texts(self, texts):
            from app.services.embedding_service import EmbeddingBatch

            return EmbeddingBatch(
                vectors=[[0.9, 0.1] for _ in texts],
                provider="sentence_transformers",
                model="new-model",
                dimensions=2,
            )

    class Row:
        embedding_json = [0.1, 0.9]
        metadata_json = {
            "embedding": {"provider": "sentence_transformers", "model": "old-model", "dimensions": 2}
        }
        text = "new evidence"

    index = SQLiteVectorIndex()
    index.embedding_service = FakeEmbedding()
    vectors, migrated = index._row_vectors(
        [Row()],
        expected_dimensions=2,
        expected_embedding={"provider": "sentence_transformers", "model": "new-model"},
    )

    assert migrated == 1
    assert vectors == [[0.9, 0.1]]


def test_query_migrates_legacy_profile_views_and_restores_fact_links(db_session):
    profile = Profile(
        name="Legacy",
        source_type="pdf",
        raw_resume_text="Built CareerAgent with RAG.",
        structured_profile_json={
            "skills": ["RAG"],
            "projects": [{"name": "CareerAgent", "description": "Built CareerAgent with RAG."}],
        },
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    index = SQLiteVectorIndex()
    index.upsert_profile_chunks(
        db_session,
        profile.id,
        [
            TextChunk(
                "structured_project_0",
                "name: CareerAgent | description: Built CareerAgent with RAG.",
                "project",
                "profile.projects",
                {"field": "projects", "item_index": 0},
            ),
            TextChunk(
                "pdf_page_1_0",
                "CareerAgent project: Built CareerAgent with RAG.",
                "raw_text",
                "profile.pdf_page_text",
                {},
            ),
        ],
    )
    for row in db_session.query(ResumeChunk).filter(ResumeChunk.profile_id == profile.id):
        metadata = dict(row.metadata_json or {})
        metadata.pop("profile_index_version", None)
        metadata.pop("fact_id", None)
        row.metadata_json = metadata
    db_session.commit()

    hits = index.query_profile_chunks(db_session, profile.id, "CareerAgent RAG", top_k=3)

    rows = db_session.query(ResumeChunk).filter(ResumeChunk.profile_id == profile.id).all()
    assert rows
    assert all((row.metadata_json or {}).get("profile_index_version") == PROFILE_INDEX_VERSION for row in rows)
    raw = next(row for row in rows if row.source == "profile.pdf_page_text")
    assert raw.metadata_json.get("fact_id") == "projects:0"
    project_hits = [hit for hit in hits if hit.chunk_type == "project"]
    assert len(project_hits) == 1
    assert project_hits[0].metadata.get("evidence_view_count") == 2


def test_multi_fact_pdf_view_does_not_consume_another_fact_top_k_slot():
    candidates = [
        RetrievedChunk(1, "project_0", "project zero", "project", "profile.projects", 0.90, {"fact_id": "projects:0"}),
        RetrievedChunk(2, "project_1", "project one", "project", "profile.projects", 0.80, {"fact_id": "projects:1"}),
        RetrievedChunk(
            3,
            "pdf_page_1",
            "page with both projects",
            "raw_text",
            "profile.pdf_page_text",
            0.95,
            {"fact_links": [{"fact_id": "projects:0"}, {"fact_id": "projects:1"}]},
        ),
    ]

    ranked = SQLiteVectorIndex._deduplicate_fact_views(candidates, top_k=2)

    assert [item.chunk_uid for item in ranked] == ["project_0", "project_1"]
    assert ranked[0].metadata.get("evidence_view_count") == 2
