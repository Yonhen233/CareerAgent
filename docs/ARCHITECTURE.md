# Architecture

CareerAgent is organized around an observable Agent workflow rather than a single prompt call.

## Layers

- `app/api`: FastAPI routers. They validate requests, load DB sessions, and delegate to services.
- `app/frontend`: Server-rendered Jinja routes for the local product UI.
- `app/agents`: Workflow orchestration and task-level control.
- `app/services`: Domain services for parsing, search, matching, RAG, tailoring, guardrails, application packets, and tracing.
- `app/models`: SQLAlchemy entities and Pydantic API schemas.
- `app/core`: configuration, database, and LLM client infrastructure.

## Data Model

- `profiles`: candidate profile from PDF or guided answers.
- `resume_chunks`: structured and raw resume chunks with deterministic embeddings.
- `jobs`: manual or discovered job descriptions.
- `match_results`: explainable profile-job matching results.
- `resume_versions`: tailored resume markdown, diff, evidence, keyword alignment, and verification.
- `applications`: quick-apply packet and tracking status.
- `agent_runs`, `agent_steps`, `agent_artifacts`: workflow observability.

## Agent Tasks

### `find_jobs_for_profile`

1. Load profile.
2. Search real job sources.
3. Persist and dedupe jobs.
4. Match profile against each job.
5. Return ranked matches and source errors.

### `tailor_resume_for_job`

1. Load profile and job.
2. Create match result.
3. Retrieve RAG evidence chunks from SQLite.
4. Generate tailored resume with LLM or deterministic fallback.
5. Verify unsupported claims and keyword coverage.
6. Save resume version and artifact.

### `quick_apply`

1. Load profile and job.
2. Reuse or create a tailored resume version.
3. Generate cover letter and outreach message.
4. Save an application packet with checklist and apply URL.

## RAG Design

CareerAgent uses SQLite as the source of truth and retrieval store:

- Structured chunks: skills, projects, experience, education.
- Raw chunks: PDF text windows.
- Embedding: deterministic feature hashing into a fixed-dimensional vector.
- Scoring: cosine similarity plus lexical token overlap.

This makes the core workflow testable without paid embedding APIs while preserving the RAG architecture expected in an Agent project.

## LLM Boundary

The LLM client is OpenAI-compatible and configured with:

- `LLM_API_KEY`
- `LLM_BASE_URL`
- `LLM_MODEL`

Online LLM calls are optional. Every core path has a deterministic fallback for local testing and demos.

## Guardrails

The guardrail service checks:

- Numeric claims in the generated resume that are absent from source evidence.
- Many newly introduced long tokens that may indicate unsupported claims.
- Required-skill keyword coverage.
- Evidence coverage from retrieved chunks.

The result is stored with each resume version.
