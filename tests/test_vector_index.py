from app.models.entities import Profile
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


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
