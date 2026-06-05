# Development Notes

## Local Setup

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload
```

## Database

CareerAgent uses SQLite by default:

```env
DATABASE_URL=sqlite:///./data/career_agent.db
```

Tables are created on startup through SQLAlchemy metadata. Runtime DB files and uploads are ignored by Git.

## Job Sources

The job search layer is source-based:

- `TencentCareersSource`: public Tencent Careers position query endpoint.
- `LeverCareersSource`: public Lever postings API for configurable slugs.

Each source maps remote rows into a normalized `JobPosting`. `JobSearchService` handles source errors, dedupe, JD parsing, and persistence.

Add a new source by implementing:

```python
class MySource(JobSource):
    name = "my_source"

    async def search(self, *, query: str, location: str | None, limit: int) -> list[JobPosting]:
        ...
```

Then register it in `JobSourceRegistry`.

## Testing

```bash
pytest -q
```

Tests use an in-memory SQLite database for service and workflow coverage. Networked job sources are not required for the regression suite.

## Coding Principles

- Keep deterministic fallbacks for parsing and generation.
- Treat the LLM as an enhancement, not as the only execution path.
- Store source evidence with generated artifacts.
- Never submit applications without user confirmation.
- Keep external career-site integrations respectful and failure-tolerant.

## Suggested Resume Talking Points

- Built an observable Agent workflow with FastAPI, SQLAlchemy, SQLite, and step-level trace artifacts.
- Implemented PDF chunking and SQLite-backed RAG retrieval without relying on external vector databases.
- Integrated real job sources with source-level failure isolation and dedupe.
- Designed grounded resume tailoring with evidence retrieval, diff generation, and hallucination guardrails.
- Added application packet generation and tracking while preserving human confirmation for real recruiting portals.
