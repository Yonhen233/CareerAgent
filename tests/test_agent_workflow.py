import asyncio

from app.agents.orchestrator import AgentOrchestrator
from app.agents.skills import active_skill_names_for_task
from app.agents.subagents import subagents_for_task
from app.agents.tools import AgentPlanner
from app.models.entities import Application, InterviewPrep, Job, MatchResult, Profile, ResumeVersion
from app.models.schemas import AgentRunRequest
from app.services.resume_tailor import ResumeTailorService
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


def test_tailor_resume_agent_workflow(db_session):
    profile = Profile(
        name="Candidate",
        email="candidate@example.com",
        headline="Agent developer",
        source_type="guided",
        raw_resume_text="Candidate built CareerAgent with Python, FastAPI, RAG, SQLite and Agent trace.",
        structured_profile_json={
            "name": "Candidate",
            "email": "candidate@example.com",
            "headline": "Agent developer",
            "skills": ["Python", "FastAPI", "RAG", "SQLite", "Agent"],
            "projects": [
                {
                    "name": "CareerAgent",
                    "description": "Job-search agent with PDF chunking, SQLite RAG, and observable workflow.",
                    "tech_stack": ["Python", "FastAPI", "SQLite"],
                    "impact": "End-to-end usable workflow",
                }
            ],
            "raw_text": "Candidate built CareerAgent with Python, FastAPI, RAG, SQLite and Agent trace.",
        },
    )
    job = Job(
        source="manual",
        external_id="job-1",
        title="Agent Development Intern",
        company="Tencent",
        raw_jd_text="Develop Agent workflows using FastAPI, RAG, SQLite, evaluation and guardrails.",
        structured_jd_json={
            "title": "Agent Development Intern",
            "company": "Tencent",
            "required_skills": ["FastAPI", "RAG", "SQLite"],
            "keywords": ["Agent", "guardrails"],
        },
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)
    SQLiteVectorIndex().upsert_profile_chunks(
        db_session,
        profile.id,
        ResumeTextSplitter().build_resume_chunks(profile.structured_profile_json),
    )

    run = asyncio.run(
        AgentOrchestrator().run(
            db_session,
            AgentRunRequest(task_type="tailor_resume_for_job", profile_id=profile.id, job_id=job.id),
        )
    )

    assert run.status == "completed"
    assert run.output_json["resume_version_id"] > 0
    assert run.output_json["verification"]["risk_level"] in {"low", "medium", "high"}
    assert run.output_json["execution_plan"]["context_policy"]["progressive_disclosure"] is True
    assert not any(item["name"] == "context_manager" for item in run.output_json["execution_plan"]["subagents"])


def test_tailor_resume_repairs_high_risk_draft_once(db_session, monkeypatch):
    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Built model evaluation dashboards with Python and PyTorch. No MLflow experience.",
        structured_profile_json={
            "skills": ["Python", "PyTorch", "Evaluation"],
            "projects": [
                {
                    "name": "VisionBench",
                    "description": "Built model evaluation dashboards with Python and PyTorch.",
                    "tech_stack": ["Python", "PyTorch"],
                }
            ],
            "raw_text": "Built model evaluation dashboards with Python and PyTorch. No MLflow experience.",
        },
    )
    job = Job(
        source="manual",
        external_id="job-repair",
        title="Machine Learning Platform Intern",
        company="MLWorks",
        raw_jd_text="Maintain MLflow, PyTorch baselines and model evaluation dashboards.",
        structured_jd_json={"required_skills": ["Python", "PyTorch", "MLflow", "Model Evaluation"]},
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)
    SQLiteVectorIndex().upsert_profile_chunks(
        db_session,
        profile.id,
        ResumeTextSplitter().build_resume_chunks(profile.structured_profile_json),
    )

    service = ResumeTailorService()

    def risky_draft(profile, job, evidence):
        return {
            "tailored_resume_markdown": (
                "Built model evaluation dashboards with Python and PyTorch.\n"
                "Eager to learn MLflow for platform workflows."
            ),
            "change_summary": [],
            "keyword_alignment": {"covered": ["Python", "PyTorch"], "missing": ["MLflow"], "notes": []},
        }

    monkeypatch.setattr(service, "_heuristic_tailor", risky_draft)
    monkeypatch.setattr(type(service.llm), "available", property(lambda self: False))

    version = asyncio.run(service.tailor_resume(db_session, profile, job))

    assert "MLflow" not in version.tailored_resume_markdown
    assert version.verification_json["passed"] is True
    repair = version.keyword_alignment_json["react_repair"]
    assert repair["attempted"] is True
    assert repair["attempts"][0]["after_passed"] is True


def test_full_career_flow_plan_exposes_modern_agent_boundaries():
    request = AgentRunRequest(task_type="full_career_flow", profile_id=1, query="Agent 开发实习生")
    plan = AgentPlanner().build_plan(request)

    assert plan["mode"] == "plan_execute"
    assert "resume_tailoring" in active_skill_names_for_task("full_career_flow")
    assert "interview_preparation" in active_skill_names_for_task("full_career_flow")
    assert "context_manager" not in [item["name"] for item in subagents_for_task("full_career_flow")]
    assert [step["step"] for step in plan["steps"]] == [
        "load_profile",
        "search_jobs",
        "match_and_select_job",
        "retrieve_resume_evidence",
        "tailor_resume",
        "verify_resume",
        "fit_gate",
        "create_application_packet",
        "generate_interview_prep",
    ]


def test_full_career_flow_orchestrator_runs_all_core_stages(db_session):
    profile = Profile(
        name="Candidate",
        email="candidate@example.com",
        headline="Agent developer",
        source_type="guided",
        target_roles_json=["Agent 开发实习生"],
        raw_resume_text="Built CareerAgent with Python, FastAPI, RAG, SQLite, LLM evaluation and guardrails.",
        structured_profile_json={
            "name": "Candidate",
            "email": "candidate@example.com",
            "headline": "Agent developer",
            "skills": ["Python", "FastAPI", "RAG", "SQLite", "LLM", "Evaluation", "Guardrail"],
            "projects": [{"name": "CareerAgent", "description": "Agent workflow with RAG and trace."}],
        },
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    class FakeJobSearch:
        async def search(self, db, **kwargs):
            job = Job(
                source="manual",
                external_id="full-flow-job",
                title="Agent 开发实习生",
                company="DemoAI",
                location="深圳",
                apply_url="https://example.com/jobs/agent-intern",
                raw_jd_text="负责 Agent workflow、FastAPI、RAG、SQLite、LLM evaluation 和 guardrail。",
                structured_jd_json={
                    "required_skills": ["FastAPI", "RAG", "SQLite", "LLM", "Evaluation"],
                    "keywords": ["Agent", "Guardrail"],
                },
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            return [job], {}

    class FakeMatcher:
        def create_match_result(self, db, profile, job):
            result = MatchResult(
                profile_id=profile.id,
                job_id=job.id,
                overall_score=92.5,
                dimension_scores_json={"required_skill_coverage": 100},
                matched_skills_json=["FastAPI", "RAG", "SQLite", "LLM", "Evaluation"],
                missing_skills_json=[],
                relevant_evidence_json=[{"text": "CareerAgent with RAG and trace."}],
                suggestions_json=[],
            )
            db.add(result)
            db.commit()
            db.refresh(result)
            return result

    class FakeTailor:
        async def tailor_resume(self, db, profile, job):
            version = ResumeVersion(
                profile_id=profile.id,
                job_id=job.id,
                title="Agent 开发实习生定制简历",
                tailored_resume_markdown="## Candidate\nBuilt CareerAgent with FastAPI, RAG, SQLite and LLM evaluation.",
                change_summary_json=[],
                keyword_alignment_json={"covered": ["FastAPI", "RAG", "SQLite"]},
                source_evidence_json=[{"text": "CareerAgent"}],
                verification_json={"passed": True, "risk_level": "low"},
                diff_text=None,
            )
            db.add(version)
            db.commit()
            db.refresh(version)
            return version

    class FakeApplication:
        async def create_quick_apply_packet(self, db, *, profile, job, resume_version, browser_assist=False):
            application = Application(
                profile_id=profile.id,
                job_id=job.id,
                resume_version_id=resume_version.id,
                status="ready",
                apply_url=job.apply_url,
                cover_letter="您好，我想申请 Agent 开发实习生岗位。",
                outreach_message="您好，希望交流 Agent 开发实习机会。",
                checklist_json=["确认岗位", "确认简历事实"],
                automation_result_json={"packet_validation": {"passed": True, "risk_level": "low"}},
            )
            db.add(application)
            db.commit()
            db.refresh(application)
            return application

    class FakeInterviewPrep:
        async def create_interview_prep_with_llm(self, db, *, profile, job, match_result):
            prep = InterviewPrep(
                profile_id=profile.id,
                job_id=job.id,
                match_result_id=match_result.id,
                title="Agent 开发实习生面试包",
                summary_json={"fit_level": "strong", "overall_score": 92.5},
                question_sets_json=[{"category": "项目", "questions": [{"question": "RAG 如何做二阶段排序？"}]}],
                gap_drills_json=[],
                research_checklist_json=[],
                source_evidence_json=[],
                coverage_json={"passed": True, "question_count": 1},
                generation_mode="fake",
            )
            db.add(prep)
            db.commit()
            db.refresh(prep)
            return prep

    run = asyncio.run(
        AgentOrchestrator(
            job_search=FakeJobSearch(),
            matcher=FakeMatcher(),
            tailor=FakeTailor(),
            application=FakeApplication(),
            interview_prep=FakeInterviewPrep(),
        ).run(
            db_session,
            AgentRunRequest(task_type="full_career_flow", profile_id=profile.id, query="Agent 开发实习生", limit=3),
        )
    )

    assert run.status == "completed"
    assert run.output_json["selected_job"]["title"] == "Agent 开发实习生"
    assert run.output_json["tailor"]["resume_version_id"] > 0
    assert run.output_json["application"]["application_id"] > 0
    assert run.output_json["interview_prep"]["interview_prep_id"] > 0
    assert run.output_json["execution_plan"]["task_type"] == "full_career_flow"
