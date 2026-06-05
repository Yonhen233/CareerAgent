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


def make_llm_workflow_cases() -> list[dict]:
    cases = [
        {
            "name": "agent_candidate_strong_agent_role",
            "difficulty": "easy",
            "resume_raw_text": (
                "Li Ming\nli.ming@example.com\nAgent 开发实习生候选人\n\n"
                "Skills: Python, FastAPI, RAG, SQLite, Agent workflow, Evaluation, Guardrails, Chroma.\n\n"
                "Projects\nCareerAgent: Built a job-search agent with PDF chunking, SQLite-backed RAG, "
                "Top20 reranker, LLM debug logs, Agent run trace and resume tailoring. "
                "Implemented FastAPI APIs and evaluation metrics for fit label accuracy and retrieval recall.\n\n"
                "Experience\nAI Lab Intern: maintained Python services and wrote guardrail tests for LLM workflows."
            ),
            "expected_profile_skills": ["Python", "FastAPI", "RAG", "SQLite", "Agent", "Evaluation", "Guardrails"],
            "expected_profile_keywords": ["CareerAgent", "Top20 reranker", "LLM debug logs"],
            "job": {
                "title": "Agent Development Intern",
                "company": "Demo AI",
                "jd_text": (
                    "Agent Development Intern. Build FastAPI services for Agent workflows, PDF chunking, "
                    "RAG retrieval, SQLite storage, reranker, evaluation metrics, LLM debug logs and guardrails. "
                    "Requirements: Python, FastAPI, RAG, SQLite, Agent workflow, evaluation, guardrails."
                ),
            },
            "expected_jd_skills": ["Python", "FastAPI", "RAG", "SQLite", "Agent", "Evaluation", "Guardrails"],
            "expected_fit_label": "strong_fit",
            "expected_fit_score_range": [85, 100],
            "run_tailor": True,
            "expected_tailored_keywords": ["FastAPI", "RAG", "SQLite", "Agent", "evaluation", "guardrails"],
            "forbidden_tailored_claims": ["React", "Kubernetes", "10x", "revenue"],
        },
        {
            "name": "agent_candidate_partial_llm_eval_role",
            "difficulty": "medium",
            "resume_raw_text": (
                "Li Ming\nli.ming@example.com\nAgent 开发实习生候选人\n\n"
                "Skills: Python, FastAPI, RAG, SQLite, Agent workflow, Evaluation, Guardrails.\n\n"
                "Projects\nCareerAgent: Built a job-search agent with RAG, trace logs, LLM call debugging and "
                "basic retrieval metrics. Did not build model quality dashboards or prompt regression suites."
            ),
            "expected_profile_skills": ["Python", "FastAPI", "RAG", "SQLite", "Agent", "Evaluation"],
            "expected_profile_keywords": ["CareerAgent", "LLM call debugging"],
            "job": {
                "title": "LLM Evaluation Intern",
                "company": "Demo AI",
                "jd_text": (
                    "LLM Evaluation Intern. Build prompt regression tests, model output scoring dashboards, "
                    "SQL analysis and quality review process. Requirements: Python, SQL, evaluation, dashboards, "
                    "prompt regression and model quality analysis."
                ),
            },
            "expected_jd_skills": ["Python", "SQL", "Evaluation", "Prompt Regression", "Dashboards"],
            "expected_fit_label": "partial_fit",
            "expected_fit_score_range": [50, 80],
            "run_tailor": True,
            "expected_tailored_keywords": ["Python", "Evaluation", "LLM", "RAG"],
            "forbidden_tailored_claims": ["prompt regression suite", "SQL dashboard", "weekly model report"],
        },
        {
            "name": "agent_candidate_weak_frontend_role",
            "difficulty": "easy",
            "resume_raw_text": (
                "Li Ming\nli.ming@example.com\nSkills: Python, FastAPI, RAG, SQLite, Agent workflow.\n\n"
                "Projects\nCareerAgent: Built backend APIs for RAG retrieval and resume tailoring."
            ),
            "expected_profile_skills": ["Python", "FastAPI", "RAG", "SQLite", "Agent"],
            "expected_profile_keywords": ["CareerAgent"],
            "job": {
                "title": "Frontend Design System Intern",
                "company": "Demo UI",
                "jd_text": (
                    "Frontend Design System Intern. Build React components, TypeScript utilities, CSS token systems, "
                    "visual QA, accessibility checks and Storybook documentation. Requirements: React, TypeScript, CSS."
                ),
            },
            "expected_jd_skills": ["React", "TypeScript", "CSS"],
            "expected_fit_label": "weak_fit",
            "expected_fit_score_range": [0, 45],
            "run_tailor": False,
            "expected_tailored_keywords": [],
            "forbidden_tailored_claims": [],
        },
        {
            "name": "llm_eval_candidate_strong_eval_role",
            "difficulty": "easy",
            "resume_raw_text": (
                "Chen Yu\nchenyu@example.com\nLLM Evaluation Intern Candidate\n\n"
                "Skills: Python, SQL, LLM Evaluation, Prompt Regression, Dashboards, Error Analysis.\n\n"
                "Projects\nEvalHarness: Created prompt regression tests, rubric-based LLM scoring, SQL analytics "
                "and dashboards for weekly model quality review. Logged JSON validity, refusal quality and latency."
            ),
            "expected_profile_skills": ["Python", "SQL", "Evaluation", "Prompt Regression", "Dashboards"],
            "expected_profile_keywords": ["EvalHarness", "rubric", "JSON validity"],
            "job": {
                "title": "LLM Evaluation Intern",
                "company": "ModelOps",
                "jd_text": (
                    "LLM Evaluation Intern. Own prompt regression suites, build model quality dashboards, analyze SQL "
                    "logs, define rubrics and report JSON validity and latency metrics. Requirements: Python, SQL, "
                    "LLM evaluation, dashboards, prompt regression."
                ),
            },
            "expected_jd_skills": ["Python", "SQL", "Evaluation", "Dashboards", "Prompt Regression"],
            "expected_fit_label": "strong_fit",
            "expected_fit_score_range": [85, 100],
            "run_tailor": True,
            "expected_tailored_keywords": ["prompt regression", "SQL", "dashboards", "rubric", "JSON"],
            "forbidden_tailored_claims": ["RAG production system", "mobile app"],
        },
        {
            "name": "backend_candidate_strong_platform_role",
            "difficulty": "easy",
            "resume_raw_text": (
                "Wang Hao\nwanghao@example.com\nBackend Platform Intern Candidate\n\n"
                "Skills: Python, FastAPI, PostgreSQL, Redis, Docker, Observability, SQLAlchemy.\n\n"
                "Projects\nServiceMeshLab: Implemented FastAPI services with PostgreSQL persistence, Redis caching, "
                "structured logs, Docker deployment and health checks. Improved local developer setup."
            ),
            "expected_profile_skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker", "Observability"],
            "expected_profile_keywords": ["ServiceMeshLab", "structured logs"],
            "job": {
                "title": "Backend Platform Intern",
                "company": "InfraWorks",
                "jd_text": (
                    "Backend Platform Intern. Build Python FastAPI services, PostgreSQL data models, Redis caching, "
                    "Docker packaging, structured logging and observability. Requirements: Python, FastAPI, PostgreSQL, "
                    "Redis, Docker, observability."
                ),
            },
            "expected_jd_skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker"],
            "expected_fit_label": "strong_fit",
            "expected_fit_score_range": [85, 100],
            "run_tailor": True,
            "expected_tailored_keywords": ["FastAPI", "PostgreSQL", "Redis", "Docker", "observability"],
            "forbidden_tailored_claims": ["RAG", "React", "Kubernetes cluster"],
        },
        {
            "name": "frontend_candidate_strong_frontend_role",
            "difficulty": "easy",
            "resume_raw_text": (
                "Zhao Lin\nzhaolin@example.com\nFrontend Engineering Intern Candidate\n\n"
                "Skills: React, TypeScript, CSS, Design System, Playwright, Accessibility, Storybook.\n\n"
                "Projects\nDashboardStudio: Built reusable React components, TypeScript hooks, CSS tokens, Playwright "
                "visual tests and accessibility checks for an operations dashboard."
            ),
            "expected_profile_skills": ["React", "TypeScript", "CSS", "Playwright", "Accessibility"],
            "expected_profile_keywords": ["DashboardStudio", "CSS tokens"],
            "job": {
                "title": "Frontend Engineering Intern",
                "company": "DesignOps",
                "jd_text": (
                    "Frontend Engineering Intern. Build reusable React components, TypeScript utilities, CSS token systems, "
                    "Playwright tests, accessibility checks and Storybook documentation."
                ),
            },
            "expected_jd_skills": ["React", "TypeScript", "CSS", "Playwright"],
            "expected_fit_label": "strong_fit",
            "expected_fit_score_range": [85, 100],
            "run_tailor": True,
            "expected_tailored_keywords": ["React", "TypeScript", "CSS", "Playwright", "accessibility"],
            "forbidden_tailored_claims": ["FastAPI service", "RAG"],
        },
        {
            "name": "frontend_candidate_weak_backend_role",
            "difficulty": "medium",
            "resume_raw_text": (
                "Zhao Lin\nzhaolin@example.com\nSkills: React, TypeScript, CSS, Playwright, Accessibility.\n\n"
                "Projects\nDashboardStudio: Built frontend components and visual tests. No backend persistence work."
            ),
            "expected_profile_skills": ["React", "TypeScript", "CSS", "Playwright"],
            "expected_profile_keywords": ["DashboardStudio"],
            "job": {
                "title": "Backend Platform Intern",
                "company": "InfraWorks",
                "jd_text": (
                    "Backend Platform Intern. Build Python FastAPI services, PostgreSQL schemas, Redis caching, Docker "
                    "deployment and observability. Requirements: Python, FastAPI, PostgreSQL, Redis, Docker."
                ),
            },
            "expected_jd_skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker"],
            "expected_fit_label": "weak_fit",
            "expected_fit_score_range": [0, 45],
            "run_tailor": False,
            "expected_tailored_keywords": [],
            "forbidden_tailored_claims": [],
        },
        {
            "name": "data_candidate_strong_data_role",
            "difficulty": "easy",
            "resume_raw_text": (
                "Sun Rui\nsunrui@example.com\nData Engineering Intern Candidate\n\n"
                "Skills: Python, SQL, Airflow, dbt, Data Quality, Warehouse, Great Expectations.\n\n"
                "Projects\nPipelineMonitor: Built Airflow DAGs, dbt transformations and data quality checks for warehouse tables. "
                "Reduced stale-table incidents through scheduled validation reports."
            ),
            "expected_profile_skills": ["Python", "SQL", "Airflow", "dbt", "Data Quality", "Warehouse"],
            "expected_profile_keywords": ["PipelineMonitor", "scheduled validation"],
            "job": {
                "title": "Data Engineering Intern",
                "company": "DataHub",
                "jd_text": (
                    "Data Engineering Intern. Build Airflow DAGs, dbt transformations, warehouse data models, SQL checks "
                    "and data quality monitoring. Requirements: Python, SQL, Airflow, dbt, data quality, warehouse."
                ),
            },
            "expected_jd_skills": ["Python", "SQL", "Airflow", "dbt", "Data Quality"],
            "expected_fit_label": "strong_fit",
            "expected_fit_score_range": [85, 100],
            "run_tailor": True,
            "expected_tailored_keywords": ["Airflow", "dbt", "SQL", "data quality", "warehouse"],
            "forbidden_tailored_claims": ["React", "LLM red team"],
        },
        {
            "name": "ml_candidate_strong_ml_role",
            "difficulty": "easy",
            "resume_raw_text": (
                "Liu Xin\nliuxin@example.com\nMachine Learning Intern Candidate\n\n"
                "Skills: Python, PyTorch, Transformers, MLflow, Model Evaluation, Feature Store.\n\n"
                "Projects\nModelLab: Trained transformer baselines in PyTorch, logged experiments with MLflow, compared metrics "
                "across datasets and documented model evaluation results."
            ),
            "expected_profile_skills": ["Python", "PyTorch", "Transformers", "MLflow", "Evaluation"],
            "expected_profile_keywords": ["ModelLab", "transformer baselines"],
            "job": {
                "title": "Machine Learning Intern",
                "company": "MLWorks",
                "jd_text": (
                    "Machine Learning Intern. Train PyTorch models, run transformer experiments, log metrics with MLflow, "
                    "compare model evaluation results and maintain feature datasets. Requirements: Python, PyTorch, "
                    "Transformers, MLflow, model evaluation."
                ),
            },
            "expected_jd_skills": ["Python", "PyTorch", "Transformers", "MLflow", "Evaluation"],
            "expected_fit_label": "strong_fit",
            "expected_fit_score_range": [85, 100],
            "run_tailor": True,
            "expected_tailored_keywords": ["PyTorch", "Transformers", "MLflow", "evaluation"],
            "forbidden_tailored_claims": ["FastAPI RAG", "Airflow DAG"],
        },
        {
            "name": "ml_candidate_partial_agent_role",
            "difficulty": "hard",
            "resume_raw_text": (
                "Liu Xin\nliuxin@example.com\nSkills: Python, PyTorch, Transformers, MLflow, Model Evaluation.\n\n"
                "Projects\nModelLab: Trained transformer baselines and compared metrics. Read papers about RAG but did not build an agent system."
            ),
            "expected_profile_skills": ["Python", "PyTorch", "Transformers", "MLflow", "Evaluation"],
            "expected_profile_keywords": ["ModelLab", "did not build an agent system"],
            "job": {
                "title": "Agent Development Intern",
                "company": "Demo AI",
                "jd_text": (
                    "Agent Development Intern. Build FastAPI services for Agent workflows, RAG retrieval, SQLite storage, "
                    "tool execution traces, evaluation metrics and guardrails. Requirements: Python, FastAPI, RAG, SQLite, Agent."
                ),
            },
            "expected_jd_skills": ["Python", "FastAPI", "RAG", "SQLite", "Agent"],
            "expected_fit_label": "partial_fit",
            "expected_fit_score_range": [45, 75],
            "run_tailor": True,
            "expected_tailored_keywords": ["Python", "Transformers", "evaluation"],
            "forbidden_tailored_claims": ["built FastAPI services", "SQLite RAG", "tool execution traces"],
        },
        {
            "name": "security_candidate_strong_ai_security_role",
            "difficulty": "medium",
            "resume_raw_text": (
                "Gao Ning\ngaoning@example.com\nAI Security Intern Candidate\n\n"
                "Skills: Python, Threat Modeling, Prompt Injection, Red Teaming, Policy, Logging, Guardrails.\n\n"
                "Projects\nGuardLab: Designed prompt-injection tests, policy checks and logging for an LLM assistant red-team workflow. "
                "Documented unsafe tool-use cases and mitigation rules."
            ),
            "expected_profile_skills": ["Python", "Threat Modeling", "Prompt Injection", "Red Teaming", "Policy", "Logging"],
            "expected_profile_keywords": ["GuardLab", "unsafe tool-use"],
            "job": {
                "title": "AI Security Intern",
                "company": "SecureAI",
                "jd_text": (
                    "AI Security Intern. Build prompt injection tests, red-team workflows, threat modeling notes, policy checks "
                    "and logging for LLM assistants. Requirements: Python, prompt injection, red teaming, policy, logging."
                ),
            },
            "expected_jd_skills": ["Python", "Prompt Injection", "Red Teaming", "Policy", "Logging"],
            "expected_fit_label": "strong_fit",
            "expected_fit_score_range": [85, 100],
            "run_tailor": True,
            "expected_tailored_keywords": ["prompt injection", "red-team", "policy", "logging"],
            "forbidden_tailored_claims": ["mobile app", "CTR ranking"],
        },
        {
            "name": "mobile_candidate_strong_mobile_ai_role",
            "difficulty": "easy",
            "resume_raw_text": (
                "He Qian\nheqian@example.com\nMobile AI Intern Candidate\n\n"
                "Skills: Kotlin, Android, On-device ML, REST, SQLite, Performance.\n\n"
                "Projects\nPocketTutor: Built an Android study assistant with on-device intent classification, REST sync, SQLite cache "
                "and cold-start performance tuning."
            ),
            "expected_profile_skills": ["Kotlin", "Android", "On-device ML", "REST", "SQLite", "Performance"],
            "expected_profile_keywords": ["PocketTutor", "cold-start"],
            "job": {
                "title": "Mobile AI Intern",
                "company": "MobileMind",
                "jd_text": (
                    "Mobile AI Intern. Build Android features in Kotlin, integrate on-device ML, REST sync, SQLite cache "
                    "and performance profiling. Requirements: Kotlin, Android, on-device ML, REST, SQLite, performance."
                ),
            },
            "expected_jd_skills": ["Kotlin", "Android", "On-device ML", "SQLite", "Performance"],
            "expected_fit_label": "strong_fit",
            "expected_fit_score_range": [85, 100],
            "run_tailor": True,
            "expected_tailored_keywords": ["Android", "Kotlin", "SQLite", "performance"],
            "forbidden_tailored_claims": ["FastAPI backend", "Airflow"],
        },
        {
            "name": "analytics_candidate_partial_recommendation_role",
            "difficulty": "hard",
            "resume_raw_text": (
                "Tang Wei\ntangwei@example.com\nProduct Analytics Intern Candidate\n\n"
                "Skills: SQL, Python, Experiment Analysis, Metrics, Funnels, Dashboards.\n\n"
                "Projects\nMetricStudio: Built funnel dashboards, experiment analysis notebooks and metric definitions. "
                "Analyzed A/B tests but did not implement ranking models or CTR features."
            ),
            "expected_profile_skills": ["SQL", "Python", "Experiment Analysis", "Metrics", "Dashboards"],
            "expected_profile_keywords": ["MetricStudio", "did not implement ranking"],
            "job": {
                "title": "Recommendation Algorithm Intern",
                "company": "RankWorks",
                "jd_text": (
                    "Recommendation Algorithm Intern. Build ranking features, analyze CTR, run offline evaluation, "
                    "feature engineering and A/B testing for recommender systems. Requirements: Python, ranking, CTR, "
                    "feature engineering, A/B testing, metrics."
                ),
            },
            "expected_jd_skills": ["Python", "Ranking", "CTR", "Feature Engineering", "A/B Testing", "Metrics"],
            "expected_fit_label": "partial_fit",
            "expected_fit_score_range": [45, 75],
            "run_tailor": True,
            "expected_tailored_keywords": ["Python", "A/B", "metrics", "experiment"],
            "forbidden_tailored_claims": ["ranking model", "CTR feature engineering"],
        },
        {
            "name": "beginner_candidate_weak_agent_role",
            "difficulty": "adversarial",
            "resume_raw_text": (
                "Wu Fan\nwufan@example.com\nBeginner Software Student\n\n"
                "Skills: Python basics, HTML, CSS, Git.\n\n"
                "Coursework\nRead articles about RAG, Agent, FastAPI and SQLite. No shipped project, no API service, no evaluation harness."
            ),
            "expected_profile_skills": ["Python", "CSS"],
            "expected_profile_keywords": ["No shipped project", "Coursework"],
            "job": {
                "title": "Agent Development Intern",
                "company": "Demo AI",
                "jd_text": (
                    "Agent Development Intern. Build production-like Agent workflows, FastAPI APIs, RAG retrieval, SQLite storage, "
                    "LLM debugging, evaluation metrics and guardrails. Requirements: Python, FastAPI, RAG, SQLite, Agent, evaluation."
                ),
            },
            "expected_jd_skills": ["Python", "FastAPI", "RAG", "SQLite", "Agent", "Evaluation"],
            "expected_fit_label": "weak_fit",
            "expected_fit_score_range": [0, 40],
            "run_tailor": False,
            "expected_tailored_keywords": [],
            "forbidden_tailored_claims": [],
        },
        {
            "name": "devops_candidate_strong_platform_role",
            "difficulty": "medium",
            "resume_raw_text": (
                "Qin Yue\nqinyue@example.com\nDevOps Platform Intern Candidate\n\n"
                "Skills: Docker, Kubernetes, CI/CD, Terraform, Prometheus, Linux.\n\n"
                "Projects\nDeployFlow: Built containerized CI/CD workflows, Kubernetes deployment templates, Terraform modules and "
                "Prometheus alert rules. Documented rollback steps and release validation."
            ),
            "expected_profile_skills": ["Docker", "Kubernetes", "CI/CD", "Terraform", "Prometheus", "Linux"],
            "expected_profile_keywords": ["DeployFlow", "rollback"],
            "job": {
                "title": "DevOps Platform Intern",
                "company": "CloudOps",
                "jd_text": (
                    "DevOps Platform Intern. Build Docker images, Kubernetes templates, CI/CD workflows, Terraform modules, "
                    "Prometheus alerts and Linux automation. Requirements: Docker, Kubernetes, CI/CD, Terraform, Prometheus, Linux."
                ),
            },
            "expected_jd_skills": ["Docker", "Kubernetes", "CI/CD", "Terraform", "Prometheus", "Linux"],
            "expected_fit_label": "strong_fit",
            "expected_fit_score_range": [85, 100],
            "run_tailor": True,
            "expected_tailored_keywords": ["Docker", "Kubernetes", "Terraform", "Prometheus", "CI/CD"],
            "forbidden_tailored_claims": ["RAG", "React"],
        },
        {
            "name": "cv_candidate_strong_cv_role",
            "difficulty": "easy",
            "resume_raw_text": (
                "Ma Chen\nmachen@example.com\nComputer Vision Intern Candidate\n\n"
                "Skills: Python, OpenCV, PyTorch, Detection, Data Augmentation, Evaluation.\n\n"
                "Projects\nVisionBench: Built an object-detection pipeline with OpenCV preprocessing, PyTorch training, data augmentation "
                "and evaluation dashboards for model comparison."
            ),
            "expected_profile_skills": ["Python", "OpenCV", "PyTorch", "Detection", "Evaluation"],
            "expected_profile_keywords": ["VisionBench", "object-detection"],
            "job": {
                "title": "Computer Vision Intern",
                "company": "VisionAI",
                "jd_text": (
                    "Computer Vision Intern. Build OpenCV preprocessing, PyTorch detection models, data augmentation, "
                    "evaluation dashboards and dataset debugging tools. Requirements: Python, OpenCV, PyTorch, detection, evaluation."
                ),
            },
            "expected_jd_skills": ["Python", "OpenCV", "PyTorch", "Detection", "Evaluation"],
            "expected_fit_label": "strong_fit",
            "expected_fit_score_range": [85, 100],
            "run_tailor": True,
            "expected_tailored_keywords": ["OpenCV", "PyTorch", "detection", "evaluation"],
            "forbidden_tailored_claims": ["LLM agent", "Airflow DAG"],
        },
        {
            "name": "cv_candidate_partial_ml_platform_role",
            "difficulty": "medium",
            "resume_raw_text": (
                "Ma Chen\nmachen@example.com\nSkills: Python, OpenCV, PyTorch, Detection, Evaluation.\n\n"
                "Projects\nVisionBench: Built object-detection experiments and evaluation dashboards. No MLflow or feature store experience."
            ),
            "expected_profile_skills": ["Python", "OpenCV", "PyTorch", "Detection", "Evaluation"],
            "expected_profile_keywords": ["No MLflow", "VisionBench"],
            "job": {
                "title": "Machine Learning Platform Intern",
                "company": "MLWorks",
                "jd_text": (
                    "Machine Learning Platform Intern. Maintain MLflow experiment tracking, feature store pipelines, model evaluation "
                    "dashboards and PyTorch baselines. Requirements: Python, PyTorch, MLflow, Feature Store, model evaluation."
                ),
            },
            "expected_jd_skills": ["Python", "PyTorch", "MLflow", "Feature Store", "Evaluation"],
            "expected_fit_label": "partial_fit",
            "expected_fit_score_range": [45, 75],
            "run_tailor": True,
            "expected_tailored_keywords": ["Python", "PyTorch", "evaluation"],
            "forbidden_tailored_claims": ["MLflow", "feature store pipelines"],
        },
        {
            "name": "data_candidate_weak_mobile_role",
            "difficulty": "medium",
            "resume_raw_text": (
                "Sun Rui\nsunrui@example.com\nSkills: Python, SQL, Airflow, dbt, Warehouse, Data Quality.\n\n"
                "Projects\nPipelineMonitor: Built data pipelines and warehouse quality checks."
            ),
            "expected_profile_skills": ["Python", "SQL", "Airflow", "dbt", "Data Quality"],
            "expected_profile_keywords": ["PipelineMonitor"],
            "job": {
                "title": "Mobile AI Intern",
                "company": "MobileMind",
                "jd_text": (
                    "Mobile AI Intern. Build Android features in Kotlin, on-device ML, REST sync, SQLite cache and performance profiling. "
                    "Requirements: Kotlin, Android, on-device ML, SQLite, performance."
                ),
            },
            "expected_jd_skills": ["Kotlin", "Android", "On-device ML", "SQLite", "Performance"],
            "expected_fit_label": "weak_fit",
            "expected_fit_score_range": [0, 45],
            "run_tailor": False,
            "expected_tailored_keywords": [],
            "forbidden_tailored_claims": [],
        },
    ]
    return cases


def main() -> None:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    pdf_cases = make_pdf_chunk_cases()
    rag_cases = make_rag_cases()
    llm_cases = make_llm_workflow_cases()
    (EVAL_DIR / "pdf_chunk_cases.json").write_text(
        json.dumps(pdf_cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (EVAL_DIR / "rag_cases.json").write_text(
        json.dumps(rag_cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (EVAL_DIR / "llm_workflow_cases.json").write_text(
        json.dumps(llm_cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    pdf_queries = sum(len(case["queries"]) for case in pdf_cases)
    rag_chunks = sum(len(case["evidence_chunks"]) for case in rag_cases)
    print(f"generated pdf_chunk_cases={len(pdf_cases)} pdf_queries={pdf_queries}")
    print(f"generated rag_cases={len(rag_cases)} rag_chunks={rag_chunks}")
    print(f"generated llm_workflow_cases={len(llm_cases)}")


if __name__ == "__main__":
    main()
