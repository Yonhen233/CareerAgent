import time
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Application, Job, Profile, ResumeVersion
from app.models.schemas import AgentRunRequest
from app.services.application_service import ApplicationService
from app.services.job_search import JobSearchService
from app.services.matcher import MatcherService
from app.services.resume_tailor import ResumeTailorService
from app.services.trace_service import TraceService


class AgentOrchestrator:
    def __init__(self) -> None:
        self.trace = TraceService()
        self.job_search = JobSearchService()
        self.matcher = MatcherService()
        self.tailor = ResumeTailorService()
        self.application = ApplicationService()

    async def run(self, db: Session, request: AgentRunRequest):
        started = time.perf_counter()
        run = self.trace.create_run(
            db,
            task_type=request.task_type,
            profile_id=request.profile_id,
            job_id=request.job_id,
            input_json=request.model_dump(),
        )
        try:
            if request.task_type == "find_jobs_for_profile":
                output = await self._find_jobs_for_profile(db, run.id, request)
            elif request.task_type == "tailor_resume_for_job":
                output = await self._tailor_resume_for_job(db, run.id, request)
            elif request.task_type == "quick_apply":
                output = await self._quick_apply(db, run.id, request)
            else:
                raise ValueError(f"Unsupported task_type: {request.task_type}")
            return self.trace.finish_run(db, run=run, status="completed", output_json=output, started_at=started)
        except Exception as exc:  # noqa: BLE001
            return self.trace.finish_run(
                db,
                run=run,
                status="failed",
                output_json={"error": str(exc)},
                error_message=str(exc),
                started_at=started,
            )

    async def _find_jobs_for_profile(self, db: Session, run_id: int, request: AgentRunRequest) -> dict[str, Any]:
        profile = await self.trace.step(
            db,
            run_id=run_id,
            step_name="load_profile",
            tool_name="ProfileRepository",
            input_json={"profile_id": request.profile_id},
            handler=lambda: self._load_profile(db, request.profile_id),
        )
        query = request.query or " ".join(profile.target_roles_json or []) or "Agent intern"
        jobs, source_errors = await self.trace.step(
            db,
            run_id=run_id,
            step_name="search_jobs",
            tool_name="JobSearchService",
            input_json={"query": query, "location": request.location, "limit": request.limit},
            handler=lambda: self.job_search.search(
                db,
                query=query,
                location=request.location,
                internship_only=True,
                limit=request.limit,
                store_results=True,
            ),
        )
        matches = []
        for job in jobs:
            match = self.matcher.create_match_result(db, profile, job)
            matches.append(
                {
                    "job_id": job.id,
                    "title": job.title,
                    "company": job.company,
                    "overall_score": match.overall_score,
                    "matched_skills": match.matched_skills_json,
                    "missing_skills": match.missing_skills_json,
                    "apply_url": job.apply_url,
                }
            )
        matches.sort(key=lambda item: item["overall_score"], reverse=True)
        payload = {"profile_id": profile.id, "query": query, "matches": matches, "source_errors": source_errors}
        self.trace.add_artifact(db, run_id=run_id, artifact_type="ranked_jobs", payload=payload)
        return payload

    async def _tailor_resume_for_job(self, db: Session, run_id: int, request: AgentRunRequest) -> dict[str, Any]:
        profile = await self.trace.step(
            db,
            run_id=run_id,
            step_name="load_profile",
            tool_name="ProfileRepository",
            input_json={"profile_id": request.profile_id},
            handler=lambda: self._load_profile(db, request.profile_id),
        )
        job = await self.trace.step(
            db,
            run_id=run_id,
            step_name="load_job",
            tool_name="JobRepository",
            input_json={"job_id": request.job_id},
            handler=lambda: self._load_job(db, request.job_id),
        )
        match = await self.trace.step(
            db,
            run_id=run_id,
            step_name="match_job",
            tool_name="MatcherService",
            input_json={"profile_id": profile.id, "job_id": job.id},
            handler=lambda: self._async_value(self.matcher.create_match_result(db, profile, job)),
        )
        version = await self.trace.step(
            db,
            run_id=run_id,
            step_name="tailor_resume_with_rag",
            tool_name="ResumeTailorService",
            input_json={"profile_id": profile.id, "job_id": job.id},
            handler=lambda: self.tailor.tailor_resume(db, profile, job),
        )
        payload = {
            "profile_id": profile.id,
            "job_id": job.id,
            "match_result_id": match.id,
            "overall_score": match.overall_score,
            "resume_version_id": version.id,
            "verification": version.verification_json,
        }
        self.trace.add_artifact(db, run_id=run_id, artifact_type="tailored_resume", payload=payload)
        return payload

    async def _quick_apply(self, db: Session, run_id: int, request: AgentRunRequest) -> dict[str, Any]:
        profile = await self._load_profile(db, request.profile_id)
        job = await self._load_job(db, request.job_id)
        resume_version = None
        if request.resume_version_id:
            resume_version = db.query(ResumeVersion).filter(ResumeVersion.id == request.resume_version_id).first()
        if resume_version is None:
            resume_version = await self.trace.step(
                db,
                run_id=run_id,
                step_name="create_missing_tailored_resume",
                tool_name="ResumeTailorService",
                input_json={"profile_id": profile.id, "job_id": job.id},
                handler=lambda: self.tailor.tailor_resume(db, profile, job),
            )
        application = await self.trace.step(
            db,
            run_id=run_id,
            step_name="create_application_packet",
            tool_name="ApplicationService",
            input_json={"profile_id": profile.id, "job_id": job.id, "resume_version_id": resume_version.id},
            handler=lambda: self.application.create_quick_apply_packet(
                db,
                profile=profile,
                job=job,
                resume_version=resume_version,
                browser_assist=False,
            ),
        )
        return self._application_payload(application)

    async def _load_profile(self, db: Session, profile_id: int | None) -> Profile:
        if profile_id is None:
            raise ValueError("profile_id is required.")
        profile = db.query(Profile).filter(Profile.id == profile_id).first()
        if profile is None:
            raise ValueError(f"Profile {profile_id} not found.")
        return profile

    async def _load_job(self, db: Session, job_id: int | None) -> Job:
        if job_id is None:
            raise ValueError("job_id is required.")
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            raise ValueError(f"Job {job_id} not found.")
        return job

    async def _async_value(self, value):
        return value

    def _application_payload(self, application: Application) -> dict[str, Any]:
        return {
            "application_id": application.id,
            "profile_id": application.profile_id,
            "job_id": application.job_id,
            "resume_version_id": application.resume_version_id,
            "status": application.status,
            "apply_url": application.apply_url,
            "checklist": application.checklist_json,
        }
