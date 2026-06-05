import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "evals"


DOMAINS = [
    {
        "name": "agent_rag",
        "role": "Agent Development Intern",
        "skills": ["Python", "FastAPI", "RAG", "SQLite", "Agent", "Evaluation", "Guardrails"],
        "project": "CareerAgent",
        "evidence": "Built a traceable job-search agent with PDF chunking, SQLite RAG, JD matching, evaluation and resume tailoring.",
        "impact": "Improved application workflow from resume upload to tailored application packet.",
    },
    {
        "name": "llm_eval",
        "role": "LLM Evaluation Intern",
        "skills": ["Python", "Evaluation", "Prompt Regression", "LLM", "SQL", "Dashboards"],
        "project": "EvalHarness",
        "evidence": "Created a prompt regression harness with scoring rules, SQL analytics and weekly model quality reports.",
        "impact": "Reduced manual review effort and caught response-format regressions.",
    },
    {
        "name": "backend_platform",
        "role": "Backend Platform Intern",
        "skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "Observability"],
        "project": "ServiceMeshLab",
        "evidence": "Implemented FastAPI services with PostgreSQL persistence, Redis caching, structured logs and Docker deployment.",
        "impact": "Improved API latency and local developer setup.",
    },
    {
        "name": "frontend_tools",
        "role": "Frontend Engineering Intern",
        "skills": ["React", "TypeScript", "CSS", "Design System", "Playwright", "Accessibility"],
        "project": "DashboardStudio",
        "evidence": "Built reusable React components, accessibility checks and Playwright tests for an operations dashboard.",
        "impact": "Improved consistency across internal tools.",
    },
    {
        "name": "ml_platform",
        "role": "Machine Learning Intern",
        "skills": ["Python", "PyTorch", "Transformers", "Feature Store", "Model Evaluation", "MLflow"],
        "project": "ModelLab",
        "evidence": "Trained transformer baselines, logged experiments with MLflow and compared model metrics across datasets.",
        "impact": "Established a repeatable model evaluation workflow.",
    },
    {
        "name": "data_engineering",
        "role": "Data Engineering Intern",
        "skills": ["Python", "SQL", "Airflow", "dbt", "Data Quality", "Warehouse"],
        "project": "PipelineMonitor",
        "evidence": "Built Airflow DAGs, dbt transformations and data quality checks for warehouse tables.",
        "impact": "Reduced stale-table incidents in analytics workflows.",
    },
]


NOISE_BLOCKS = [
    "Coursework: operating systems, database systems, distributed systems, software engineering and product analytics.",
    "Awards: university scholarship, programming contest finalist, open-source contribution certificate.",
    "Activities: reading group organizer, AI product club member, technical blog author.",
    "Tools: Git, Linux, VS Code, REST APIs, JSON, Markdown, CI checks and command-line automation.",
]

ALIAS_QUERIES = {
    "RAG": "retrieval augmented generation",
    "FastAPI": "Python API service",
    "SQLite": "embedded relational storage",
    "Agent": "autonomous workflow orchestration",
    "Evaluation": "model quality measurement",
    "Guardrails": "safety checks",
    "React": "component based user interface",
    "TypeScript": "typed frontend code",
    "PyTorch": "deep learning framework",
    "Airflow": "scheduled data pipelines",
}


def long_noise(seed: int, repeat: int = 5) -> str:
    parts = []
    for offset in range(repeat):
        parts.append(NOISE_BLOCKS[(seed + offset) % len(NOISE_BLOCKS)])
    return " ".join(parts)


def make_pdf_chunk_cases() -> list[dict]:
    cases = []
    for idx in range(30):
        domain = DOMAINS[idx % len(DOMAINS)]
        distractor = DOMAINS[(idx + 2) % len(DOMAINS)]
        pages = [
            {
                "page_no": 1,
                "text": "\n\n".join(
                    [
                        f"Candidate {idx:02d}",
                        f"Target Role: {domain['role']}",
                        long_noise(idx, repeat=3),
                        "Skills: " + ", ".join(domain["skills"]),
                        NOISE_BLOCKS[idx % len(NOISE_BLOCKS)],
                    ]
                ),
            },
            {
                "page_no": 2,
                "text": "\n\n".join(
                    [
                        "Projects",
                        long_noise(idx + 1, repeat=4),
                        f"{domain['project']}: {domain['evidence']} {domain['impact']}",
                        "Implementation Details: "
                        + " ".join(
                            [
                                f"Used {domain['skills'][0]} with {domain['skills'][1]} and {domain['skills'][2]}."
                                for _ in range(4)
                            ]
                        ),
                        f"{distractor['project']}: Assisted with {distractor['skills'][0]} and documentation for a classroom prototype.",
                        NOISE_BLOCKS[(idx + 1) % len(NOISE_BLOCKS)],
                    ]
                ),
            },
            {
                "page_no": 3,
                "text": "\n\n".join(
                    [
                        "Experience",
                        long_noise(idx + 2, repeat=4),
                        f"Internship: supported {domain['skills'][1]} implementation, wrote tests and documented release notes.",
                        "Delivery Notes: "
                        + " ".join(
                            [
                                f"Verified {domain['skills'][1]} behavior and documented {domain['skills'][2]} tradeoffs."
                                for _ in range(3)
                            ]
                        ),
                        "Education: Computer Science, expected graduation 2027.",
                        NOISE_BLOCKS[(idx + 2) % len(NOISE_BLOCKS)],
                    ]
                ),
            },
        ]
        queries = [
            {
                "query": f"{domain['role']} {domain['skills'][0]} {domain['skills'][1]}",
                "expected_keyword": domain["skills"][1],
                "expected_page": 1,
                "expected_context_keywords": [domain["skills"][0], domain["skills"][1]],
            },
            {
                "query": f"project evidence for {domain['project']} {domain['skills'][2]}",
                "expected_keyword": domain["project"],
                "expected_page": 2,
                "expected_context_keywords": [domain["project"], domain["skills"][2], domain["impact"].split()[0]],
            },
            {
                "query": f"impact of {domain['project']}",
                "expected_keyword": domain["impact"].split()[0],
                "expected_page": 2,
                "expected_context_keywords": [domain["project"], domain["impact"].split()[0]],
            },
            {
                "query": f"internship implementation {domain['skills'][1]} tests",
                "expected_keyword": "Internship",
                "expected_page": 3,
                "expected_context_keywords": ["Internship", domain["skills"][1], "tests"],
            },
        ]
        cases.append({"name": f"pdf_chunk_case_{idx:02d}_{domain['name']}", "pages": pages, "queries": queries})
    return cases


def make_rag_cases() -> list[dict]:
    cases = []
    for idx in range(48):
        domain = DOMAINS[idx % len(DOMAINS)]
        distractor_a = DOMAINS[(idx + 1) % len(DOMAINS)]
        distractor_b = DOMAINS[(idx + 3) % len(DOMAINS)]
        evidence_chunks = [
            {
                "chunk_id": f"{idx}_target_project",
                "chunk_type": "project",
                "text": f"{domain['project']}: {domain['evidence']} {domain['impact']}",
                "expected": True,
            },
            {
                "chunk_id": f"{idx}_target_skills",
                "chunk_type": "skill",
                "text": "Skills: " + ", ".join(domain["skills"]),
                "expected": True,
            },
            {
                "chunk_id": f"{idx}_target_experience",
                "chunk_type": "experience",
                "text": f"Experience: supported {domain['skills'][1]} delivery, tests, monitoring and documentation.",
                "expected": True,
            },
            {
                "chunk_id": f"{idx}_distractor_a",
                "chunk_type": "project",
                "text": f"{distractor_a['project']}: {distractor_a['evidence']}",
                "expected": False,
            },
            {
                "chunk_id": f"{idx}_distractor_b",
                "chunk_type": "experience",
                "text": f"Experience: built prototypes around {distractor_b['skills'][0]}, {distractor_b['skills'][1]} and reporting.",
                "expected": False,
            },
            {
                "chunk_id": f"{idx}_noise",
                "chunk_type": "education",
                "text": NOISE_BLOCKS[idx % len(NOISE_BLOCKS)],
                "expected": False,
            },
        ]
        query_terms = []
        for skill in domain["skills"][:4]:
            query_terms.append(ALIAS_QUERIES.get(skill, skill) if idx % 2 == 0 else skill)
        jd = (
            f"{domain['role']}. Responsibilities include {', '.join(query_terms)}, "
            "evidence-backed delivery, measurable quality and production-ready documentation."
        )
        cases.append(
            {
                "name": f"rag_case_{idx:02d}_{domain['name']}",
                "query": jd,
                "evidence_chunks": evidence_chunks,
                "expected_chunk_ids": [
                    f"{idx}_target_project",
                    f"{idx}_target_skills",
                    f"{idx}_target_experience",
                ],
            }
        )
    return cases


def main() -> None:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / "pdf_chunk_cases.json").write_text(
        json.dumps(make_pdf_chunk_cases(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (EVAL_DIR / "rag_cases.json").write_text(
        json.dumps(make_rag_cases(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("generated pdf_chunk_cases=30 rag_cases=48")


if __name__ == "__main__":
    main()
