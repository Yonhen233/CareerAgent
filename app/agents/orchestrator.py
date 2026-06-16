import time
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Application, Job, Profile, ResumeVersion
from app.models.schemas import AgentRunRequest
from app.agents.tools import AgentPlanner
from app.services.application_service import ApplicationService
from app.services.interview_prep import InterviewPrepService
from app.services.job_search import JobSearchService
from app.services.matcher import MatcherService
from app.services.resume_tailor import ResumeTailorService
from app.services.trace_service import TraceService


class AgentOrchestrator:
    def __init__(
        self,
        *,
        trace: TraceService | None = None,
        job_search: JobSearchService | None = None,
        matcher: MatcherService | None = None,
        tailor: ResumeTailorService | None = None,
        application: ApplicationService | None = None,
        interview_prep: InterviewPrepService | None = None,
        planner: AgentPlanner | None = None,
    ) -> None:
        self.trace = trace or TraceService()
        self.job_search = job_search or JobSearchService()
        self.matcher = matcher or MatcherService()
        self.tailor = tailor or ResumeTailorService()
        self.application = application or ApplicationService()
        self.interview_prep = interview_prep or InterviewPrepService()
        self.planner = planner or AgentPlanner()

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
            plan = await self.trace.step(
                db,
                run_id=run.id,
                step_name="plan_task",
                tool_name="AgentPlanner",
                input_json={"task_type": request.task_type},
                handler=lambda: self._async_value(self.planner.build_plan(request)),
            )
            self.trace.add_artifact(db, run_id=run.id, artifact_type="execution_plan", payload=plan)
            if request.task_type == "find_jobs_for_profile":
                output = await self._find_jobs_for_profile(db, run.id, request)
            elif request.task_type == "tailor_resume_for_job":
                output = await self._tailor_resume_for_job(db, run.id, request)
            elif request.task_type == "quick_apply":
                output = await self._quick_apply(db, run.id, request)
            elif request.task_type == "prepare_interview_for_job":
                output = await self._prepare_interview_for_job(db, run.id, request)
            elif request.task_type == "full_career_flow":
                output = await self._full_career_flow(db, run.id, request)
            else:
                raise ValueError(f"Unsupported task_type: {request.task_type}")
            output["execution_plan"] = plan
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
        query = request.query or " ".join(profile.target_roles_json or []) or "Agent 开发实习生"
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
        fit_gate = await self.trace.step(
            db,
            run_id=run_id,
            step_name="fit_gate",
            tool_name="MatcherService",
            input_json={"profile_id": profile.id, "job_id": job.id, "min_score": 55},
            handler=lambda: self._async_value(self._fit_gate(db, profile, job)),
        )
        self.trace.add_artifact(db, run_id=run_id, artifact_type="fit_gate", payload=fit_gate)
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
        payload = self._application_payload(application)
        payload["fit_gate"] = fit_gate
        return payload

    async def _prepare_interview_for_job(self, db: Session, run_id: int, request: AgentRunRequest) -> dict[str, Any]:
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
        prep = await self.trace.step(
            db,
            run_id=run_id,
            step_name="generate_interview_prep",
            tool_name="InterviewPrepService",
            input_json={"profile_id": profile.id, "job_id": job.id, "match_result_id": match.id},
            handler=lambda: self.interview_prep.create_interview_prep_with_llm(
                db, profile=profile, job=job, match_result=match
            ),
        )
        payload = self._interview_prep_payload(prep)
        self.trace.add_artifact(db, run_id=run_id, artifact_type="interview_prep", payload=payload)
        return payload

    async def _full_career_flow(self, db: Session, run_id: int, request: AgentRunRequest) -> dict[str, Any]:
        find_payload = await self._find_jobs_for_profile(db, run_id, request)
        matches = find_payload.get("matches") or []
        if not matches:
            raise ValueError(
                "Full career flow stopped: no matched jobs found. "
                f"source_errors={find_payload.get('source_errors') or {}}"
            )
        selected_job = matches[0]
        selected_job_id = int(selected_job["job_id"])
        self.trace.add_artifact(
            db,
            run_id=run_id,
            artifact_type="selected_job",
            payload={"selection_policy": "highest_overall_score", "selected_job": selected_job},
        )

        base = request.model_copy(update={"job_id": selected_job_id})
        tailor_payload = await self._tailor_resume_for_job(
            db,
            run_id,
            base.model_copy(update={"task_type": "tailor_resume_for_job"}),
        )
        apply_payload = await self._quick_apply(
            db,
            run_id,
            base.model_copy(
                update={
                    "task_type": "quick_apply",
                    "resume_version_id": tailor_payload.get("resume_version_id"),
                }
            ),
        )
        interview_payload = await self._prepare_interview_for_job(
            db,
            run_id,
            base.model_copy(update={"task_type": "prepare_interview_for_job"}),
        )
        payload = {
            "profile_id": request.profile_id,
            "query": find_payload.get("query"),
            "selected_job": selected_job,
            "matches": matches,
            "source_errors": find_payload.get("source_errors") or {},
            "tailor": tailor_payload,
            "application": apply_payload,
            "interview_prep": interview_payload,
            "links": {
                "profile": f"/ui/profiles?profile_id={request.profile_id}",
                "job": f"/ui/jobs?job_id={selected_job_id}",
                "resume_versions": "/ui/resumes",
                "applications": "/ui/applications",
                "interview_prep": f"/ui/prep?job_id={selected_job_id}",
                "trace": "/ui/agent-runs",
            },
        }
        self.trace.add_artifact(db, run_id=run_id, artifact_type="full_career_flow", payload=payload)
        return payload

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

    def _fit_gate(self, db: Session, profile: Profile, job: Job) -> dict[str, Any]:
        match = self.matcher.create_match_result(db, profile, job)
        payload = {
            "match_result_id": match.id,
            "overall_score": match.overall_score,
            "matched_skills": match.matched_skills_json,
            "missing_skills": match.missing_skills_json,
            "passed": match.overall_score >= 55,
            "min_score": 55,
        }
        if not payload["passed"]:
            raise ValueError(
                f"Fit gate blocked quick_apply: score {match.overall_score} is below 55. "
                f"Missing skills: {', '.join(match.missing_skills_json[:6])}"
            )
        return payload

    def _application_payload(self, application: Application) -> dict[str, Any]:
        automation_result = application.automation_result_json or {}
        return {
            "application_id": application.id,
            "profile_id": application.profile_id,
            "job_id": application.job_id,
            "resume_version_id": application.resume_version_id,
            "status": application.status,
            "apply_url": application.apply_url,
            "checklist": application.checklist_json,
            "packet_validation": automation_result.get("packet_validation"),
            "automation_result": automation_result,
        }

    def _interview_prep_payload(self, prep) -> dict[str, Any]:
        return {
            "interview_prep_id": prep.id,
            "profile_id": prep.profile_id,
            "job_id": prep.job_id,
            "match_result_id": prep.match_result_id,
            "title": prep.title,
            "summary": prep.summary_json,
            "coverage": prep.coverage_json,
            "question_set_count": len(prep.question_sets_json or []),
            "gap_drill_count": len(prep.gap_drills_json or []),
            "research_item_count": len(prep.research_checklist_json or []),
        }
