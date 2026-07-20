import asyncio

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Job
from app.services.jd_parser import JDParserService
from app.services.job_relevance import is_internship_like_posting, rank_postings_for_query
from app.services.job_sources import JobPosting, JobSourceRegistry
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


class JobSearchService:
    def __init__(self) -> None:
        self.registry = JobSourceRegistry()
        self.jd_parser = JDParserService()
        self.splitter = ResumeTextSplitter()
        self.vector_index = SQLiteVectorIndex()
        self.settings = get_settings()

    async def search(
        self,
        db: Session,
        *,
        query: str,
        location: str | None = None,
        internship_only: bool = True,
        limit: int = 20,
        sources: list[str] | None = None,
        store_results: bool = True,
    ) -> tuple[list[Job], dict[str, str]]:
        selected = self.registry.select(sources)
        source_errors: dict[str, str] = {}

        async def _run_source(source):
            try:
                postings = await source.search(query=query, location=location, limit=limit)
                return source.name, postings, None
            except Exception as exc:  # noqa: BLE001
                return source.name, [], str(exc)

        source_runs = await asyncio.gather(*[_run_source(source) for source in selected])
        postings: list[JobPosting] = []
        for source_name, rows, error in source_runs:
            if error:
                source_errors[source_name] = error
            postings.extend(rows)

        if internship_only:
            postings = [posting for posting in postings if self._is_internship_like(posting)]

        postings = rank_postings_for_query(self._dedupe_postings(postings), query)[:limit]
        parsed_postings = await self._parse_postings_concurrently(postings)

        jobs: list[Job] = []
        for posting, structured in parsed_postings:
            if store_results:
                jobs.append(self.upsert_prepared_posting(db, posting, structured))
            else:
                jobs.append(
                    Job(
                        source=posting.source,
                        external_id=posting.external_id,
                        title=posting.title,
                        company=posting.company,
                        location=posting.location,
                        job_type=posting.job_type,
                        apply_url=posting.apply_url,
                        raw_jd_text=posting.raw_jd_text,
                        structured_jd_json=structured,
                        source_payload_json=posting.payload,
                    )
                )
        return jobs, source_errors

    async def upsert_posting(self, db: Session, posting: JobPosting) -> Job:
        structured = await self.jd_parser.parse_jd(
            posting.raw_jd_text,
            title=posting.title,
            company=posting.company,
            location=posting.location,
        )
        return self.upsert_prepared_posting(db, posting, structured)

    def upsert_prepared_posting(self, db: Session, posting: JobPosting, structured: dict) -> Job:
        external_id = posting.external_id or f"{posting.title}:{posting.company}:{posting.apply_url}"
        existing = (
            db.query(Job)
            .filter(Job.source == posting.source, Job.external_id == external_id)
            .first()
        )
        if existing:
            existing.title = posting.title
            existing.company = posting.company
            existing.location = posting.location
            existing.job_type = posting.job_type
            existing.apply_url = posting.apply_url
            existing.raw_jd_text = posting.raw_jd_text
            existing.structured_jd_json = structured
            existing.source_payload_json = posting.payload
            db.commit()
            db.refresh(existing)
            self._index_job_chunks(db, existing)
            return existing

        job = Job(
            source=posting.source,
            external_id=external_id,
            title=posting.title,
            company=posting.company,
            location=posting.location,
            job_type=posting.job_type,
            apply_url=posting.apply_url,
            raw_jd_text=posting.raw_jd_text,
            structured_jd_json=structured,
            source_payload_json=posting.payload,
        )
        db.add(job)
        try:
            db.commit()
            db.refresh(job)
            self._index_job_chunks(db, job)
            return job
        except IntegrityError:
            db.rollback()
            existing = (
                db.query(Job)
                .filter(Job.source == posting.source, Job.external_id == external_id)
                .first()
            )
            if existing is None:
                raise
            self._index_job_chunks(db, existing)
            return existing

    async def _parse_postings_concurrently(self, postings: list[JobPosting]) -> list[tuple[JobPosting, dict]]:
        semaphore = asyncio.Semaphore(max(1, self.settings.job_ingest_concurrency))

        async def _parse(posting: JobPosting) -> tuple[JobPosting, dict]:
            async with semaphore:
                structured = self.jd_parser.parse_jd_for_search(
                    posting.raw_jd_text,
                    title=posting.title,
                    company=posting.company,
                    location=posting.location,
                )
                return posting, structured

        return await asyncio.gather(*[_parse(posting) for posting in postings])

    def _index_job_chunks(self, db: Session, job: Job) -> int:
        chunks = self.splitter.split_jd_text(job.raw_jd_text, job.structured_jd_json or {}, prefix=f"job_{job.id}")
        return self.vector_index.upsert_job_chunks(db, job.id, chunks)

    def _dedupe_postings(self, postings: list[JobPosting]) -> list[JobPosting]:
        seen: set[tuple[str, str]] = set()
        output: list[JobPosting] = []
        for posting in postings:
            key = (posting.source, posting.external_id or posting.apply_url or posting.title.lower())
            if key in seen:
                continue
            seen.add(key)
            output.append(posting)
        return output

    def _is_internship_like(self, posting: JobPosting) -> bool:
        return is_internship_like_posting(posting)
