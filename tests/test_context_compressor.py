from app.models.entities import Job, Profile
from app.services.context_compressor import ContextCompressor


def test_context_compressor_uses_hierarchical_progressive_disclosure():
    long_resume = " ".join(
        [
            "Candidate built CareerAgent with FastAPI, SQLite, RAG, evaluation, guardrail and agent trace."
            for _ in range(140)
        ]
    )
    profile = Profile(
        name="Candidate",
        headline="Agent developer",
        raw_resume_text=long_resume,
        structured_profile_json={
            "name": "Candidate",
            "headline": "Agent developer",
            "skills": ["Python", "FastAPI", "RAG", "SQLite", "Agent", "Evaluation"] * 8,
            "projects": [
                {
                    "name": f"Project {index}",
                    "description": "Implemented retrieval, reranking, PDF chunk evaluation and trace logging. " * 8,
                    "tech_stack": ["Python", "FastAPI", "SQLite", "RAG"],
                    "impact": "Measured pass rates and failure stages. " * 5,
                }
                for index in range(10)
            ],
            "work_experience": [
                {
                    "company": f"Company {index}",
                    "role": "Backend intern",
                    "duration": "2025",
                    "details": "Delivered async APIs, job ingestion and LLM logging. " * 8,
                    "tech_stack": ["Python", "FastAPI"],
                }
                for index in range(8)
            ],
        },
    )
    job = Job(
        title="Agent Development Intern",
        company="Example AI",
        raw_jd_text=" ".join(
            [
                "Build agent workflows with FastAPI, RAG, SQLite, reranker, evaluation and guardrails."
                for _ in range(100)
            ]
        ),
        structured_jd_json={
            "required_skills": ["FastAPI", "RAG", "SQLite", "Agent", "Evaluation"] * 8,
            "responsibilities": ["Build production agent workflows and evaluation traces."] * 10,
            "qualifications": ["Experience with RAG, async API and vector retrieval."] * 10,
            "keywords": ["Agent", "RAG", "Guardrail"] * 10,
        },
    )
    evidence = [
        {
            "chunk_uid": f"chunk-{index}",
            "chunk_type": "project",
            "score": 0.9 - index * 0.01,
            "source": "test",
            "text": "CareerAgent evidence: FastAPI RAG SQLite evaluation guardrail trace. " * 18,
            "metadata": {"retrieval": {"rank": index + 1}, "rerank": {"score": 0.8}},
        }
        for index in range(20)
    ]

    compressed = ContextCompressor().compress_tailor_context(profile=profile, job=job, evidence=evidence)
    metadata = compressed["context_compression"]

    assert metadata["strategy"] == "hierarchical_progressive_disclosure"
    assert metadata["raw_chars"] > metadata["compressed_chars"]
    assert metadata["retained_evidence_count"] <= 20
    assert len(metadata["levels"]) >= 4
    assert compressed["progressive_disclosure"]["failure_rule"]
    assert "ranked_evidence" in compressed
