import asyncio

from app.models.entities import Job
from app.services.jd_parser import JDParserService
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


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
