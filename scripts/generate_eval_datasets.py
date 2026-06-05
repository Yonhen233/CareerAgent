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
        "hard_negative": "LLM evaluation dashboard that analyzes prompts but does not implement retrieval or agent execution.",
    },
    {
        "name": "llm_eval",
        "role": "LLM Evaluation Intern",
        "skills": ["Python", "Evaluation", "Prompt Regression", "LLM", "SQL", "Dashboards"],
        "project": "EvalHarness",
        "evidence": "Created a prompt regression harness with scoring rules, SQL analytics and weekly model quality reports.",
        "impact": "Reduced manual review effort and caught response-format regressions.",
        "hard_negative": "Agent demo that calls an LLM once but has no regression metrics or judge rubric.",
    },
    {
        "name": "backend_platform",
        "role": "Backend Platform Intern",
        "skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "Observability"],
        "project": "ServiceMeshLab",
        "evidence": "Implemented FastAPI services with PostgreSQL persistence, Redis caching, structured logs and Docker deployment.",
        "impact": "Improved API latency and local developer setup.",
        "hard_negative": "Python notebook prototype without API persistence, deployment, Redis or service observability.",
    },
    {
        "name": "frontend_tools",
        "role": "Frontend Engineering Intern",
        "skills": ["React", "TypeScript", "CSS", "Design System", "Playwright", "Accessibility"],
        "project": "DashboardStudio",
        "evidence": "Built reusable React components, accessibility checks and Playwright tests for an operations dashboard.",
        "impact": "Improved consistency across internal tools.",
        "hard_negative": "Static HTML resume page with CSS only and no component state, tests or accessibility review.",
    },
    {
        "name": "ml_platform",
        "role": "Machine Learning Intern",
        "skills": ["Python", "PyTorch", "Transformers", "Feature Store", "Model Evaluation", "MLflow"],
        "project": "ModelLab",
        "evidence": "Trained transformer baselines, logged experiments with MLflow and compared model metrics across datasets.",
        "impact": "Established a repeatable model evaluation workflow.",
        "hard_negative": "SQL dashboard that reports model counts but does not train or evaluate models.",
    },
    {
        "name": "data_engineering",
        "role": "Data Engineering Intern",
        "skills": ["Python", "SQL", "Airflow", "dbt", "Data Quality", "Warehouse"],
        "project": "PipelineMonitor",
        "evidence": "Built Airflow DAGs, dbt transformations and data quality checks for warehouse tables.",
        "impact": "Reduced stale-table incidents in analytics workflows.",
        "hard_negative": "Manual spreadsheet cleanup with SQL notes but no scheduled pipeline or warehouse checks.",
    },
    {
        "name": "devops_platform",
        "role": "DevOps Platform Intern",
        "skills": ["Docker", "Kubernetes", "CI/CD", "Terraform", "Prometheus", "Linux"],
        "project": "DeployFlow",
        "evidence": "Built containerized CI/CD workflows, Kubernetes deployment templates and Prometheus alert rules.",
        "impact": "Shortened release validation time and made rollback steps auditable.",
        "hard_negative": "Local Docker Compose demo with no cluster deployment, alerts or infrastructure state.",
    },
    {
        "name": "security_ai",
        "role": "AI Security Intern",
        "skills": ["Python", "Threat Modeling", "Prompt Injection", "Red Teaming", "Policy", "Logging"],
        "project": "GuardLab",
        "evidence": "Designed prompt-injection tests, policy checks and logging for an LLM assistant red-team workflow.",
        "impact": "Found unsafe tool-use cases before release and documented mitigation rules.",
        "hard_negative": "Generic auth middleware project with no LLM threat model or red-team coverage.",
    },
    {
        "name": "mobile_ai",
        "role": "Mobile AI Intern",
        "skills": ["Kotlin", "Android", "On-device ML", "REST", "SQLite", "Performance"],
        "project": "PocketTutor",
        "evidence": "Built an Android study assistant with on-device intent classification, REST sync and SQLite cache.",
        "impact": "Reduced cold-start latency and supported offline review sessions.",
        "hard_negative": "Responsive web page that mentions mobile layout but has no Android or on-device inference.",
    },
    {
        "name": "recommendation",
        "role": "Recommendation Algorithm Intern",
        "skills": ["Python", "Ranking", "CTR", "Feature Engineering", "A/B Testing", "Metrics"],
        "project": "RankLab",
        "evidence": "Implemented ranking features, offline CTR evaluation and A/B metric analysis for recommendation experiments.",
        "impact": "Improved experiment reproducibility and clarified feature importance.",
        "hard_negative": "Search autocomplete UI with no ranking model, CTR analysis or experiment metrics.",
    },
    {
        "name": "analytics_engineering",
        "role": "Product Analytics Intern",
        "skills": ["SQL", "Python", "Experiment Analysis", "Metrics", "Funnels", "Dashboards"],
        "project": "MetricStudio",
        "evidence": "Built funnel dashboards, experiment analysis notebooks and metric definitions for product decisions.",
        "impact": "Reduced ambiguous metric interpretation across product reviews.",
        "hard_negative": "Backend logging library that emits events but has no funnel or experiment analysis.",
    },
    {
        "name": "computer_vision",
        "role": "Computer Vision Intern",
        "skills": ["Python", "OpenCV", "PyTorch", "Detection", "Data Augmentation", "Evaluation"],
        "project": "VisionBench",
        "evidence": "Built an object-detection pipeline with OpenCV preprocessing, PyTorch training and evaluation dashboards.",
        "impact": "Improved dataset debugging and model comparison speed.",
        "hard_negative": "Image gallery app that displays photos but does not train or evaluate vision models.",
    },
]


NOISE_BLOCKS = [
    "Coursework: operating systems, database systems, distributed systems, software engineering and product analytics.",
    "Awards: university scholarship, programming contest finalist, open-source contribution certificate.",
    "Activities: reading group organizer, AI product club member, technical blog author.",
    "Tools: Git, Linux, VS Code, REST APIs, JSON, Markdown, CI checks and command-line automation.",
    "Planned learning: the candidate plans to study Kubernetes, RAG, dashboards and model evaluation next semester.",
    "Unrelated notes: wrote classroom reports about APIs, retrieval, ranking, mobile UI and data warehouses.",
    "Rejected prototype: an early demo was abandoned because it had no tests, no persistence and no trace logs.",
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
    "Kubernetes": "container orchestration",
    "CI/CD": "release automation",
    "Threat Modeling": "risk analysis",
    "Prompt Injection": "LLM attack testing",
    "On-device ML": "local inference",
    "Ranking": "candidate ordering model",
    "Experiment Analysis": "A/B result analysis",
    "OpenCV": "image processing library",
}

DIFFICULTIES = ["easy", "medium", "hard", "adversarial"]


def long_noise(seed: int, repeat: int = 8) -> str:
    return " ".join(NOISE_BLOCKS[(seed + offset) % len(NOISE_BLOCKS)] for offset in range(repeat))


def alias_or_skill(skill: str, *, use_alias: bool) -> str:
    return ALIAS_QUERIES.get(skill, skill) if use_alias else skill


def make_pdf_chunk_cases() -> list[dict]:
    cases = []
    case_count = 96
    for idx in range(case_count):
        domain = DOMAINS[idx % len(DOMAINS)]
        distractor_a = DOMAINS[(idx + 1) % len(DOMAINS)]
        distractor_b = DOMAINS[(idx + 5) % len(DOMAINS)]
        difficulty = DIFFICULTIES[idx % len(DIFFICULTIES)]
        pages = [
            {
                "page_no": 1,
                "text": "\n\n".join(
                    [
                        f"Candidate {idx:03d}",
                        f"Target Role: {domain['role']}",
                        long_noise(idx, repeat=5),
                        "Skills Summary: " + ", ".join(domain["skills"][:4]),
                        f"Non evidence note: mentioned {distractor_a['skills'][1]} in a class discussion only.",
                        long_noise(idx + 1, repeat=4),
                    ]
                ),
            },
            {
                "page_no": 2,
                "text": "\n\n".join(
                    [
                        "Primary Project",
                        long_noise(idx + 2, repeat=6),
                        f"{domain['project']}: {domain['evidence']} {domain['impact']}",
                        "Implementation Details: "
                        + " ".join(
                            [
                                f"Used {domain['skills'][0]} with {domain['skills'][1]} and {domain['skills'][2]}."
                                for _ in range(3)
                            ]
                        ),
                        f"Hard negative project: {distractor_a['project']} looked similar, but {distractor_a['hard_negative']}",
                        long_noise(idx + 3, repeat=5),
                    ]
                ),
            },
            {
                "page_no": 3,
                "text": "\n\n".join(
                    [
                        "Internship Experience",
                        long_noise(idx + 4, repeat=7),
                        f"Internship: supported {domain['skills'][1]} implementation, wrote tests and documented release notes.",
                        "Delivery Notes: "
                        + " ".join(
                            [
                                f"Verified {domain['skills'][1]} behavior and documented {domain['skills'][2]} tradeoffs."
                                for _ in range(3)
                            ]
                        ),
                        f"Negative evidence: planned to learn {distractor_b['skills'][2]}, but did not build it.",
                        long_noise(idx + 5, repeat=4),
                    ]
                ),
            },
            {
                "page_no": 4,
                "text": "\n\n".join(
                    [
                        "Secondary Projects And Noise",
                        long_noise(idx + 6, repeat=8),
                        f"{distractor_b['project']}: assisted with {distractor_b['skills'][0]} notes and documentation for a course prototype.",
                        f"Course project: compared {domain['skills'][2]} terminology with {distractor_b['skills'][2]} but did not ship a system.",
                        "This page intentionally repeats common words: Python API data model evaluation dashboard agent pipeline.",
                        long_noise(idx + 7, repeat=5),
                    ]
                ),
            },
            {
                "page_no": 5,
                "text": "\n\n".join(
                    [
                        "Metrics And Evidence Appendix",
                        long_noise(idx + 8, repeat=6),
                        f"Evidence Appendix: {domain['project']} used {domain['skills'][3]} and {domain['skills'][4]} with traceable tests.",
                        f"Measured Outcome: {domain['impact']}",
                        "Appendix noise: awards, coursework, personal interests, planned learning and unrelated tools.",
                        long_noise(idx + 9, repeat=5),
                    ]
                ),
            },
        ]
        use_alias = difficulty in {"hard", "adversarial"}
        queries = [
            {
                "query": f"{domain['role']} {alias_or_skill(domain['skills'][0], use_alias=use_alias)} {alias_or_skill(domain['skills'][1], use_alias=use_alias)}",
                "expected_keyword": domain["skills"][1],
                "expected_page": 1,
                "expected_context_keywords": [domain["skills"][0], domain["skills"][1]],
                "difficulty": difficulty,
                "noise_profile": "skill_summary_with_adjacent_skill_noise",
            },
            {
                "query": f"project evidence for {domain['project']} {alias_or_skill(domain['skills'][2], use_alias=use_alias)}",
                "expected_keyword": domain["project"],
                "expected_page": 2,
                "expected_context_keywords": [domain["project"], domain["skills"][2], domain["impact"].split()[0]],
                "difficulty": difficulty,
                "noise_profile": "hard_negative_project_same_page",
            },
            {
                "query": f"implemented internship work for {alias_or_skill(domain['skills'][1], use_alias=use_alias)} with tests not just planned learning",
                "expected_keyword": "Internship",
                "expected_page": 3,
                "expected_context_keywords": ["Internship", domain["skills"][1], "tests"],
                "difficulty": difficulty,
                "noise_profile": "planned_learning_negative",
            },
            {
                "query": f"appendix evidence {domain['project']} {alias_or_skill(domain['skills'][3], use_alias=use_alias)} {alias_or_skill(domain['skills'][4], use_alias=use_alias)}",
                "expected_keyword": "Evidence Appendix",
                "expected_page": 5,
                "expected_context_keywords": [domain["project"], domain["skills"][3], domain["skills"][4]],
                "difficulty": difficulty,
                "noise_profile": "late_page_appendix",
            },
            {
                "query": f"avoid confusing {distractor_b['project']} with shipped {domain['project']} impact",
                "expected_keyword": domain["impact"].split()[0],
                "expected_page": 5,
                "expected_context_keywords": [domain["project"], domain["impact"].split()[0]],
                "difficulty": difficulty,
                "noise_profile": "cross_page_distractor",
            },
            {
                "query": f"find real shipped system evidence for {domain['role']} not coursework notes",
                "expected_keyword": domain["project"],
                "expected_page": 2,
                "expected_context_keywords": [domain["project"], "Built"],
                "difficulty": difficulty,
                "noise_profile": "coursework_vs_shipped",
            },
        ]
        cases.append(
            {
                "name": f"pdf_chunk_case_{idx:03d}_{domain['name']}_{difficulty}",
                "difficulty": difficulty,
                "pages": pages,
                "queries": queries,
            }
        )
    return cases


def make_rag_cases() -> list[dict]:
    cases = []
    case_count = 180
    for idx in range(case_count):
        domain = DOMAINS[idx % len(DOMAINS)]
        distractor_a = DOMAINS[(idx + 1) % len(DOMAINS)]
        distractor_b = DOMAINS[(idx + 4) % len(DOMAINS)]
        distractor_c = DOMAINS[(idx + 7) % len(DOMAINS)]
        difficulty = DIFFICULTIES[idx % len(DIFFICULTIES)]
        use_alias = difficulty in {"hard", "adversarial"}
        evidence_chunks = [
            {
                "chunk_id": f"{idx}_target_project",
                "chunk_type": "project",
                "text": f"{domain['project']}: {domain['evidence']} {domain['impact']}",
                "expected": True,
                "noise_profile": "target",
            },
            {
                "chunk_id": f"{idx}_target_skills",
                "chunk_type": "skill",
                "text": "Skills: " + ", ".join(domain["skills"]),
                "expected": True,
                "noise_profile": "target",
            },
            {
                "chunk_id": f"{idx}_target_experience",
                "chunk_type": "experience",
                "text": f"Experience: supported {domain['skills'][1]} delivery, tests, monitoring and documentation.",
                "expected": True,
                "noise_profile": "target",
            },
            {
                "chunk_id": f"{idx}_target_metric",
                "chunk_type": "project",
                "text": f"Metric evidence: {domain['project']} produced auditable outcomes. {domain['impact']}",
                "expected": True,
                "noise_profile": "target_metric",
            },
            {
                "chunk_id": f"{idx}_hard_negative_same_language",
                "chunk_type": "project",
                "text": f"{distractor_a['project']}: {distractor_a['hard_negative']} It mentions {domain['skills'][0]} and {domain['skills'][1]} but lacks shipped {domain['project']} evidence.",
                "expected": False,
                "noise_profile": "hard_negative_same_language",
            },
            {
                "chunk_id": f"{idx}_planned_learning",
                "chunk_type": "education",
                "text": f"Planned learning: wants to study {domain['skills'][2]}, {domain['skills'][3]} and {domain['skills'][4]} next semester, no implementation yet.",
                "expected": False,
                "noise_profile": "planned_learning",
            },
            {
                "chunk_id": f"{idx}_coursework_noise",
                "chunk_type": "education",
                "text": f"Coursework report discussed {domain['skills'][1]}, {distractor_b['skills'][1]} and general system design definitions.",
                "expected": False,
                "noise_profile": "coursework",
            },
            {
                "chunk_id": f"{idx}_adjacent_project",
                "chunk_type": "project",
                "text": f"{distractor_b['project']}: {distractor_b['evidence']} {distractor_b['impact']}",
                "expected": False,
                "noise_profile": "adjacent_domain",
            },
            {
                "chunk_id": f"{idx}_tools_noise",
                "chunk_type": "skill",
                "text": "Tools: Python, REST APIs, JSON, Git, Linux, dashboards, evaluation, monitoring, documentation.",
                "expected": False,
                "noise_profile": "generic_tools",
            },
            {
                "chunk_id": f"{idx}_rejected_prototype",
                "chunk_type": "project",
                "text": f"Rejected prototype: attempted {domain['skills'][2]} with no tests, no persistence and no user-facing workflow.",
                "expected": False,
                "noise_profile": "negative_outcome",
            },
            {
                "chunk_id": f"{idx}_far_distractor",
                "chunk_type": "experience",
                "text": f"Experience: wrote notes for {distractor_c['role']} using {distractor_c['skills'][0]}, {distractor_c['skills'][1]} and reports.",
                "expected": False,
                "noise_profile": "far_domain",
            },
            {
                "chunk_id": f"{idx}_long_noise",
                "chunk_type": "raw_text",
                "text": long_noise(idx, repeat=7),
                "expected": False,
                "noise_profile": "long_noise",
            },
        ]
        query_terms = [alias_or_skill(skill, use_alias=use_alias) for skill in domain["skills"][:5]]
        neg_hint = "not planned learning, not coursework, not abandoned prototype" if difficulty == "adversarial" else ""
        jd = (
            f"{domain['role']}. Responsibilities include {', '.join(query_terms)}, "
            f"evidence-backed delivery, measurable quality and production-ready documentation. {neg_hint}"
        ).strip()
        cases.append(
            {
                "name": f"rag_case_{idx:03d}_{domain['name']}_{difficulty}",
                "difficulty": difficulty,
                "noise_profiles": sorted({item["noise_profile"] for item in evidence_chunks if not item["expected"]}),
                "query": jd,
                "evidence_chunks": evidence_chunks,
                "expected_chunk_ids": [
                    f"{idx}_target_project",
                    f"{idx}_target_skills",
                    f"{idx}_target_experience",
                    f"{idx}_target_metric",
                ],
            }
        )
    return cases


def main() -> None:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    pdf_cases = make_pdf_chunk_cases()
    rag_cases = make_rag_cases()
    (EVAL_DIR / "pdf_chunk_cases.json").write_text(
        json.dumps(pdf_cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (EVAL_DIR / "rag_cases.json").write_text(
        json.dumps(rag_cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    pdf_queries = sum(len(case["queries"]) for case in pdf_cases)
    rag_chunks = sum(len(case["evidence_chunks"]) for case in rag_cases)
    print(f"generated pdf_chunk_cases={len(pdf_cases)} pdf_queries={pdf_queries}")
    print(f"generated rag_cases={len(rag_cases)} rag_chunks={rag_chunks}")


if __name__ == "__main__":
    main()
