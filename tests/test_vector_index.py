from app.models.entities import Profile
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import RetrievedChunk, SQLiteVectorIndex


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
