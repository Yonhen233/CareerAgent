import asyncio

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import Job
from app.services.jd_parser import JDParserService
from app.services.job_sources import JobPosting, JobSourceRegistry


class JobSearchService:
    def __init__(self) -> None:
        self.registry = JobSourceRegistry()
        self.jd_parser = JDParserService()

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

        runs = await asyncio.gather(*[_run_source(source) for source in selected])
        postings: list[JobPosting] = []
        for source_name, rows, error in runs:
            if error:
                source_errors[source_name] = error
            postings.extend(rows)

        if internship_only:
            postings = [posting for posting in postings if self._is_internship_like(posting)]

        postings = self._dedupe_postings(postings)
        jobs: list[Job] = []
        for posting in postings[:limit]:
            if store_results:
                jobs.append(await self.upsert_posting(db, posting))
            else:
                structured = await self.jd_parser.parse_jd(
                    posting.raw_jd_text,
                    title=posting.title,
                    company=posting.company,
                    location=posting.location,
                )
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
            return existing

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
        haystack = " ".join(
            [
                posting.title,
                posting.job_type or "",
                posting.raw_jd_text[:800],
            ]
        ).lower()
        return any(token in haystack for token in ["intern", "internship", "实习", "校招"])
