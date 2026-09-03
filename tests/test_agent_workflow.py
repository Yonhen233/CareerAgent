import asyncio

from app.agents.orchestrator import AgentOrchestrator
from app.agents.skills import active_skill_names_for_task
from app.agents.subagents import subagents_for_task
from app.agents.tools import AgentPlanner
from app.models.entities import (
    AgentArtifact,
    AgentEvent,
    AgentStep,
    Application,
    InterviewPrep,
    Job,
    MatchResult,
    Profile,
    ResumeVersion,
)
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
    assert run.input_json["orchestration_framework"] == "langgraph"
    assert run.output_json["orchestration_framework"] == "langgraph"
    assert run.output_json["execution_plan"]["orchestration_framework"] == "langgraph"
    assert run.output_json["execution_plan"]["context_policy"]["progressive_disclosure"] is True
    assert run.output_json["execution_plan"]["tool_permission_validation"]["passed"] is True
    assert run.output_json["execution_plan"]["skill_disclosure"]["instructions_in_plan"] is False
    assert not any(item["name"] == "context_manager" for item in run.output_json["execution_plan"]["subagents"])
    summary = run.output_json["business_summary"]
    assert summary["task_label"] == "定制岗位简历"
    assert summary["metrics"]["tool_call_count"] >= 3
    assert summary["metrics"]["tool_success_rate"] == 1.0
    assert summary["metrics"]["unsupported_claim_count"] == 0
    assert summary["routing_layer"]["tool_permission_validation"]["passed"] is True
    assert summary["result_layer"]["result_ids"]["resume_version_id"] == run.output_json["resume_version_id"]
    assert summary["side_effect_layer"]["approval_bypass_detected"] is False


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
    assert plan["orchestration_framework"] == "langgraph"
    assert plan["langgraph_decision"]["migrated"] is True
    assert plan["tool_permission_validation"]["passed"] is True
    assert len(plan["skill_contracts"]) == len(plan["skills"])
    assert all(item["instructions_loaded"] is False for item in plan["skill_contracts"])
    assert all(item["risk_level"] in {"low", "medium", "high"} for item in plan["tool_policies"])
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

        def retrieve_evidence_with_quality(self, db, profile_id, job, top_k=10):
            del db, profile_id, job, top_k
            return (
                [{"text": "CareerAgent with FastAPI, RAG, SQLite and LLM evaluation.", "chunk_type": "project"}],
                {"passed": True, "confidence": 1.0, "evidence_count": 1},
            )

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

    orchestrator = AgentOrchestrator(
        job_search=FakeJobSearch(),
        matcher=FakeMatcher(),
        tailor=FakeTailor(),
        application=FakeApplication(),
        interview_prep=FakeInterviewPrep(),
    )
    first = asyncio.run(
        orchestrator.run(
            db_session,
            AgentRunRequest(
                task_type="full_career_flow",
                profile_id=profile.id,
                query="Agent 开发实习生",
                limit=3,
                application_confirmed=True,
            ),
        )
    )
    assert first.status == "waiting_for_confirmation"
    assert first.output_json["confirmation_type"] == "job_selection"
    job_id = first.output_json["interrupts"][0]["value"]["matches"][0]["job_id"]
    run = asyncio.run(
        orchestrator.resume(
            db_session,
            first.id,
            {"confirmed": True, "job_id": job_id, "source": "test_job_selection"},
        )
    )

    assert run.status == "completed"
    assert run.output_json["selected_job"]["title"] == "Agent 开发实习生"
    assert run.output_json["tailor"]["resume_version_id"] > 0
    assert run.output_json["application"]["application_id"] > 0
    assert run.output_json["interview_prep"]["interview_prep_id"] > 0
    assert run.output_json["execution_plan"]["task_type"] == "full_career_flow"
    assert run.output_json["orchestration_framework"] == "langgraph"
    assert run.output_json["execution_plan"]["graph_thread_id"] == run.output_json["graph_thread_id"]
    assert db_session.query(MatchResult).count() == 1
    completed_steps = {
        row.step_name
        for row in db_session.query(AgentStep).filter(
            AgentStep.run_id == run.id,
            AgentStep.status == "completed",
        )
    }
    assert {"retrieve_resume_evidence", "tailor_resume_with_rag", "verify_resume"} <= completed_steps
    artifact_types = {
        row.artifact_type
        for row in db_session.query(AgentArtifact).filter(AgentArtifact.run_id == run.id)
    }
    assert {"resume_evidence_retrieval", "tailored_resume", "resume_verification"} <= artifact_types
    event_types = {
        row.event_type
        for row in db_session.query(AgentEvent).filter(AgentEvent.run_id == run.id).all()
    }
    assert "graph_node_started" in event_types
    assert "graph_node_completed" in event_types
    assert "step_completed" in event_types
    assert "run_finished" in event_types


def test_full_career_flow_with_target_job_skips_job_search(db_session):
    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Built CareerAgent with FastAPI, RAG, SQLite and LangGraph.",
        structured_profile_json={"skills": ["FastAPI", "RAG", "SQLite", "LangGraph"]},
    )
    job = Job(
        source="manual",
        external_id="target-job",
        title="Agent 开发实习生",
        company="DemoAI",
        apply_url="https://example.com/apply",
        raw_jd_text="负责 LangGraph Agent、FastAPI、RAG 和 SQLite。",
        structured_jd_json={"required_skills": ["FastAPI", "RAG", "SQLite", "LangGraph"]},
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)

    class FailingJobSearch:
        async def search(self, *args, **kwargs):
            raise AssertionError("full_career_flow with job_id should not search jobs")

    class FakeMatcher:
        def create_match_result(self, db, profile, job):
            result = MatchResult(
                profile_id=profile.id,
                job_id=job.id,
                overall_score=91,
                dimension_scores_json={"required_skill_coverage": 100},
                matched_skills_json=["FastAPI", "RAG", "SQLite", "LangGraph"],
                missing_skills_json=[],
                relevant_evidence_json=[],
                suggestions_json=[],
            )
            db.add(result)
            db.commit()
            db.refresh(result)
            return result

        def retrieve_evidence_with_quality(self, db, profile_id, job, top_k=10):
            del db, profile_id, job, top_k
            return (
                [{"text": "CareerAgent with LangGraph, FastAPI, RAG and SQLite.", "chunk_type": "project"}],
                {"passed": True, "confidence": 1.0, "evidence_count": 1},
            )

    class FakeTailor:
        async def tailor_resume(self, db, profile, job):
            version = ResumeVersion(
                profile_id=profile.id,
                job_id=job.id,
                title="目标岗位定制简历",
                tailored_resume_markdown="CareerAgent with LangGraph, FastAPI, RAG and SQLite.",
                change_summary_json=[],
                keyword_alignment_json={"covered": ["LangGraph", "FastAPI"]},
                source_evidence_json=[],
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
                cover_letter="申请 Agent 开发实习生。",
                outreach_message="希望交流 Agent 实习机会。",
                checklist_json=["确认事实"],
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
                title="目标岗位面试包",
                summary_json={"fit_level": "strong"},
                question_sets_json=[],
                gap_drills_json=[],
                research_checklist_json=[],
                source_evidence_json=[],
                coverage_json={"passed": True},
                generation_mode="fake",
            )
            db.add(prep)
            db.commit()
            db.refresh(prep)
            return prep

    run = asyncio.run(
        AgentOrchestrator(
            job_search=FailingJobSearch(),
            matcher=FakeMatcher(),
            tailor=FakeTailor(),
            application=FakeApplication(),
            interview_prep=FakeInterviewPrep(),
        ).run(
            db_session,
            AgentRunRequest(
                task_type="full_career_flow",
                profile_id=profile.id,
                job_id=job.id,
                application_confirmed=True,
            ),
        )
    )

    assert run.status == "completed"
    assert run.output_json["selected_job"]["title"] == "Agent 开发实习生"
    assert run.output_json["tailor"]["job_id"] == job.id
    step_names = {event.node_name for event in db_session.query(AgentEvent).filter(AgentEvent.run_id == run.id).all()}
    assert "search_jobs" not in step_names
    assert "load_job" in step_names


def test_queued_run_with_empty_search_fails_completion_gate_and_records_events(db_session, monkeypatch):
    from pathlib import Path
    from uuid import uuid4

    from app.core.config import get_settings

    checkpoint_path = Path(".tmp_test") / f"queued_checkpoints_{uuid4().hex}.sqlite"
    checkpoint_path.parent.mkdir(exist_ok=True)
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_FILE", str(checkpoint_path.resolve()))
    get_settings.cache_clear()

    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Built CareerAgent with FastAPI and RAG.",
        structured_profile_json={"skills": ["FastAPI", "RAG"]},
        target_roles_json=["Agent 开发实习生"],
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    class EmptyJobSearch:
        async def search(self, db, **kwargs):
            return [], {}

    orchestrator = AgentOrchestrator(job_search=EmptyJobSearch())
    queued = orchestrator.queue_run(
        db_session,
        AgentRunRequest(task_type="find_jobs_for_profile", profile_id=profile.id, query="Agent 开发实习生"),
    )

    assert queued.status == "queued"
    assert queued.input_json["graph_thread_id"].startswith("agent-run-")

    completed = asyncio.run(AgentOrchestrator(job_search=EmptyJobSearch()).run_existing(db_session, queued.id))

    assert completed.status == "failed"
    assert "completion gate rejected" in completed.error_message.lower()
    event_types = [
        row.event_type
        for row in db_session.query(AgentEvent).filter(AgentEvent.run_id == completed.id).order_by(AgentEvent.id).all()
    ]
    assert "run_created" in event_types
    assert "run_started" in event_types
    assert "completion_gate_rejected" in event_types
    assert "graph_failed" in event_types
    assert "run_finished" in event_types
    get_settings.cache_clear()
    checkpoint_path.unlink(missing_ok=True)


def test_quick_apply_interrupts_and_resumes_from_sqlite_checkpoint(db_session, monkeypatch):
    from pathlib import Path
    from uuid import uuid4

    from app.core.config import get_settings

    checkpoint_path = Path(".tmp_test") / f"langgraph_checkpoints_{uuid4().hex}.sqlite"
    checkpoint_path.parent.mkdir(exist_ok=True)
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_FILE", str(checkpoint_path.resolve()))
    get_settings.cache_clear()

    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Built CareerAgent with FastAPI, RAG and SQLite.",
        structured_profile_json={"skills": ["FastAPI", "RAG", "SQLite"]},
    )
    job = Job(
        source="manual",
        external_id="interrupt-job",
        title="Agent 开发实习生",
        company="DemoAI",
        apply_url="https://example.com/apply",
        raw_jd_text="负责 Agent、FastAPI、RAG 和 SQLite。",
        structured_jd_json={"required_skills": ["FastAPI", "RAG", "SQLite"]},
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)
    version = ResumeVersion(
        profile_id=profile.id,
        job_id=job.id,
        title="定制简历",
        tailored_resume_markdown="Built CareerAgent with FastAPI, RAG and SQLite.",
        change_summary_json=[],
        keyword_alignment_json={"covered": ["FastAPI", "RAG", "SQLite"]},
        source_evidence_json=[],
        verification_json={"passed": True, "risk_level": "low"},
        diff_text=None,
    )
    db_session.add(version)
    db_session.commit()
    db_session.refresh(version)

    class FakeMatcher:
        def create_match_result(self, db, profile, job):
            result = MatchResult(
                profile_id=profile.id,
                job_id=job.id,
                overall_score=88.0,
                dimension_scores_json={"required_skill_coverage": 100},
                matched_skills_json=["FastAPI", "RAG", "SQLite"],
                missing_skills_json=[],
                relevant_evidence_json=[],
                suggestions_json=[],
            )
            db.add(result)
            db.commit()
            db.refresh(result)
            return result

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

    first = asyncio.run(
        AgentOrchestrator(matcher=FakeMatcher(), application=FakeApplication()).run(
            db_session,
            AgentRunRequest(
                task_type="quick_apply",
                profile_id=profile.id,
                job_id=job.id,
                resume_version_id=version.id,
            ),
        )
    )

    assert first.status == "waiting_for_confirmation"
    assert first.output_json["requires_confirmation"] is True
    assert first.output_json["interrupts"][0]["value"]["kind"] == "application_packet_confirmation"
    assert db_session.query(Application).count() == 0

    second_orchestrator = AgentOrchestrator(matcher=FakeMatcher(), application=FakeApplication())
    graph_state = asyncio.run(second_orchestrator.graph_state(first))
    assert graph_state["interrupts"][0]["value"]["required_action"] == "confirm_before_application_packet"

    resumed = asyncio.run(
        second_orchestrator.resume(
            db_session,
            first.id,
            {"confirmed": True, "source": "test_resume", "note": "确认生成投递包"},
        )
    )

    assert resumed.status == "completed"
    assert resumed.output_json["application_id"] > 0
    assert resumed.output_json["human_confirmation"]["source"] == "test_resume"
    assert db_session.query(Application).count() == 1
    get_settings.cache_clear()
    checkpoint_path.unlink(missing_ok=True)
