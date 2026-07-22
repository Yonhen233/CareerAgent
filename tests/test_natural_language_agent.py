import asyncio
from types import SimpleNamespace

from app.agents.natural_language import NaturalLanguageAgentService
from app.models.entities import AgentEvent, Job, Profile
from app.models.schemas import NaturalLanguageAgentRequest
from app.services.trace_service import TraceService


def test_natural_language_agent_creates_profile_from_user_description(db_session, monkeypatch):
    service = NaturalLanguageAgentService()

    async def fake_plan(db, request):
        return {
            "intent": "create_profile",
            "query": "Agent 开发实习生",
            "profile": {
                "name": "李明",
                "email": "liming@example.com",
                "headline": "Agent 开发实习生候选人",
                "target_roles": ["Agent 开发实习生"],
                "skills": ["Python", "FastAPI", "RAG", "SQLite"],
                "projects": [
                    {
                        "name": "CareerAgent",
                        "description": "构建求职助手 Agent。",
                        "tech_stack": ["Python", "FastAPI"],
                        "impact": "完成可运行的求职流程。",
                    }
                ],
            },
            "needs_profile": False,
            "needs_job": False,
            "actions": ["create_profile"],
            "reason": "用户提供了完整项目描述。",
        }

    monkeypatch.setattr(service, "_build_plan", fake_plan)

    run = asyncio.run(
        service.run(
            db_session,
            NaturalLanguageAgentRequest(instruction="根据我的 CareerAgent 项目生成一份 Agent 实习简历。"),
        )
    )

    assert run.status == "completed"
    assert run.task_type == "natural_language_request"
    assert run.output_json["status"] == "completed"
    assert run.output_json["orchestration_framework"] == "langgraph"
    assert run.output_json["graph_thread_id"].startswith("natural-run-")
    assert run.output_json["result_json"]["profile"]["id"] > 0
    assert "简历档案" in run.output_json["user_message"]
    event_types = {
        row.event_type
        for row in db_session.query(AgentEvent).filter(AgentEvent.run_id == run.id).all()
    }
    assert "graph_node_started" in event_types
    assert "graph_node_completed" in event_types


def test_natural_language_plan_respects_explicit_no_tailor_constraint():
    service = NaturalLanguageAgentService()
    request = NaturalLanguageAgentRequest(
        instruction="只建立简历档案，不要搜索岗位、不要定制简历、不要投递。",
        selected_actions=["create_profile"],
    )

    plan = service._normalize_plan(
        {
            "intent": "create_profile",
            "actions": ["create_profile", "tailor_resume", "quick_apply"],
            "needs_profile": False,
            "needs_job": True,
        },
        request,
    )

    assert plan["intent"] == "create_profile"
    assert plan["actions"] == ["create_profile"]
    assert plan["needs_job"] is False


def test_natural_language_agent_repairs_missing_job_plan(db_session, monkeypatch):
    service = NaturalLanguageAgentService()

    async def bad_plan(db, request):
        return {
            "intent": "tailor_resume",
            "query": "Agent 开发实习生",
            "profile": None,
            "job": None,
            "needs_profile": True,
            "needs_job": True,
            "actions": ["tailor_resume"],
            "reason": "缺少 job 会失败。",
        }

    async def repaired_plan(db, request, plan, error):
        return {
            "intent": "create_profile",
            "query": "Agent 开发实习生",
            "profile": {
                "name": "候选人",
                "target_roles": ["Agent 开发实习生"],
                "skills": ["Python", "FastAPI"],
                "projects": [{"name": "CareerAgent", "description": "Agent 项目"}],
            },
            "job": None,
            "needs_profile": False,
            "needs_job": False,
            "actions": ["create_profile"],
            "reason": "先生成简历档案。",
        }

    monkeypatch.setattr(service, "_build_plan", bad_plan)
    monkeypatch.setattr(service, "_repair_plan", repaired_plan)

    run = asyncio.run(
        service.run(
            db_session,
            NaturalLanguageAgentRequest(instruction="帮我改简历"),
        )
    )

    assert run.status == "completed"
    assert run.output_json["repair_attempts"]
    assert run.output_json["result_json"]["profile"]["id"] > 0


def test_natural_language_agent_fails_empty_job_search_after_repair(db_session, monkeypatch):
    service = NaturalLanguageAgentService()

    async def search_plan(db, request):
        return {
            "intent": "search_jobs",
            "query": "Agent 开发实习生",
            "profile": {
                "name": "李明",
                "target_roles": ["Agent 开发实习生"],
                "skills": ["Python", "FastAPI"],
                "projects": [{"name": "CareerAgent", "description": "Agent 求职助手"}],
            },
            "job": None,
            "needs_profile": False,
            "needs_job": False,
            "actions": ["search_jobs_by_profile"],
            "reason": "用户要求按简历搜索岗位。",
        }

    async def fake_repair(db, request, plan, error):
        return await search_plan(db, request)

    async def fake_run(db, request):
        return SimpleNamespace(
            id=999,
            task_type=request.task_type,
            status="completed",
            output_json={"profile_id": request.profile_id, "query": request.query, "matches": [], "source_errors": {}},
            error_message=None,
        )

    monkeypatch.setattr(service, "_build_plan", search_plan)
    monkeypatch.setattr(service, "_repair_plan", fake_repair)
    monkeypatch.setattr(service.orchestrator, "run", fake_run)

    run = asyncio.run(
        service.run(
            db_session,
            NaturalLanguageAgentRequest(instruction="帮我搜索 Agent 开发实习岗位"),
        )
    )

    assert run.status == "failed"
    assert run.output_json["repair_attempts"]
    assert "岗位搜索没有返回可推荐岗位" in run.error_message


def test_natural_language_agent_browses_jobs_without_resume(db_session, monkeypatch):
    service = NaturalLanguageAgentService()
    job = SimpleNamespace(id=77, title="Agent 开发实习生", company="DemoAI", location="北京")

    async def search_plan(db, request):
        return {
            "intent": "search_jobs",
            "query": "Agent RAG 实习",
            "profile": None,
            "job": None,
            "needs_profile": False,
            "needs_job": False,
            "actions": ["search_jobs"],
            "reason": "不使用简历浏览岗位。",
        }

    async def fake_discover(db, payload, tenant_id=None):
        return SimpleNamespace(
            id=12,
            results=[
                SimpleNamespace(
                    job_id=job.id,
                    match_result_id=None,
                    rank=1,
                    retrieval_score=91.0,
                    match_score=None,
                    final_score=91.0,
                    reason_json={"relevance_reasons": ["Agent", "RAG"]},
                    job=job,
                )
            ],
        )

    monkeypatch.setattr(service, "_build_plan", search_plan)
    monkeypatch.setattr(service.job_discovery, "discover", fake_discover)

    run = asyncio.run(
        service.run(
            db_session,
            NaturalLanguageAgentRequest(instruction="我没有简历，只想先浏览 Agent 和 RAG 实习。"),
        )
    )

    assert run.status == "completed"
    assert run.output_json["result_json"]["job_search_session_id"] == 12
    assert run.output_json["result_json"]["matches"][0]["job_id"] == 77
    assert run.output_json["result_json"]["matches"][0]["match_score"] is None


def test_natural_language_agent_respects_selected_actions_and_profile_context(db_session, monkeypatch):
    job = Job(
        source="manual",
        external_id="selected-actions-job",
        title="Agent 开发实习生",
        company="DemoAI",
        raw_jd_text="负责 Agent workflow、RAG、FastAPI。",
        structured_jd_json={"required_skills": ["Python", "FastAPI", "RAG"]},
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    service = NaturalLanguageAgentService()
    called_tasks = []

    async def fake_plan(db, request):
        return service._normalize_plan(
            {
                "intent": "create_profile",
                "query": "Agent 开发实习生",
                "profile": None,
                "job": None,
                "needs_profile": False,
                "needs_job": False,
                "actions": [],
                "reason": "LLM 没有推断出后续动作。",
            },
            request,
        )

    async def fake_run(db, request):
        called_tasks.append(request.task_type)
        if request.task_type == "find_jobs_for_profile":
            return SimpleNamespace(
                id=901,
                task_type=request.task_type,
                status="completed",
                output_json={
                    "profile_id": request.profile_id,
                    "query": request.query,
                    "matches": [{"job_id": job.id, "title": job.title, "company": job.company, "overall_score": 88}],
                },
                error_message=None,
            )
        if request.task_type == "tailor_resume_for_job":
            return SimpleNamespace(
                id=902,
                task_type=request.task_type,
                status="completed",
                output_json={"resume_version_id": 321, "profile_id": request.profile_id, "job_id": request.job_id},
                error_message=None,
            )
        if request.task_type == "prepare_interview_for_job":
            return SimpleNamespace(
                id=903,
                task_type=request.task_type,
                status="completed",
                output_json={"interview_prep_id": 654, "profile_id": request.profile_id, "job_id": request.job_id},
                error_message=None,
            )
        raise AssertionError(request.task_type)

    monkeypatch.setattr(service, "_build_plan", fake_plan)
    monkeypatch.setattr(service.orchestrator, "run", fake_run)

    run = asyncio.run(
        service.run(
            db_session,
            NaturalLanguageAgentRequest(
                instruction="按我勾选的内容处理。",
                profile_context={
                    "name": "李明",
                    "email": "liming@example.com",
                    "target_roles": ["Agent 开发实习生"],
                    "skills": ["Python", "FastAPI", "RAG"],
                    "projects": [{"name": "CareerAgent", "description": "求职助手 Agent"}],
                },
                selected_actions=["create_profile", "search_jobs", "tailor_resume", "interview_prep"],
                query="Agent 开发实习生",
            ),
        )
    )

    assert run.status == "completed"
    assert called_tasks == ["find_jobs_for_profile", "tailor_resume_for_job", "prepare_interview_for_job"]
    assert run.output_json["plan_json"]["actions"] == [
        "create_profile",
        "search_jobs",
        "tailor_resume",
        "interview_prep",
    ]
    assert run.output_json["result_json"]["profile"]["name"] == "李明"
    assert run.output_json["result_json"]["tailor"]["resume_version_id"] == 321
    assert run.output_json["result_json"]["interview_prep"]["interview_prep_id"] == 654


def test_natural_language_agent_recovers_child_artifact_ids(db_session, monkeypatch):
    profile = Profile(
        name="浏览器回归同学",
        source_type="guided",
        raw_resume_text="CareerAgent with Python FastAPI RAG SQLite.",
        structured_profile_json={
            "name": "浏览器回归同学",
            "skills": ["Python", "FastAPI", "RAG", "SQLite"],
            "projects": [{"name": "CareerAgent", "description": "Agent 求职助手"}],
        },
    )
    job = Job(
        source="manual",
        external_id="job-natural-artifact",
        title="Agent 开发实习生",
        company="DemoAI",
        raw_jd_text="要求 Python、FastAPI、RAG、SQLite。",
        structured_jd_json={"required_skills": ["Python", "FastAPI", "RAG", "SQLite"]},
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)

    trace = TraceService()

    class ArtifactOnlyOrchestrator:
        async def run(self, db, request):
            run = trace.create_run(
                db,
                task_type=request.task_type,
                profile_id=request.profile_id,
                job_id=request.job_id,
                input_json=request.model_dump(),
            )
            if request.task_type == "tailor_resume_for_job":
                trace.add_artifact(
                    db,
                    run_id=run.id,
                    artifact_type="tailored_resume",
                    payload={"resume_version_id": 123, "profile_id": request.profile_id, "job_id": request.job_id},
                )
            elif request.task_type == "prepare_interview_for_job":
                trace.add_artifact(
                    db,
                    run_id=run.id,
                    artifact_type="interview_prep",
                    payload={"interview_prep_id": 456, "profile_id": request.profile_id, "job_id": request.job_id},
                )
            return trace.finish_run(
                db,
                run=run,
                status="completed",
                output_json={"execution_plan": {"task_type": request.task_type}},
                started_at=0.0,
            )

    service = NaturalLanguageAgentService(orchestrator=ArtifactOnlyOrchestrator())

    async def fake_plan(db, request):
        return {
            "intent": "interview_prep",
            "query": "Agent 开发实习生",
            "profile": None,
            "job": None,
            "needs_profile": True,
            "needs_job": True,
            "actions": ["tailor_resume", "interview_prep"],
            "reason": "测试子 run artifact 补齐。",
        }

    monkeypatch.setattr(service, "_build_plan", fake_plan)

    run = asyncio.run(
        service.run(
            db_session,
            NaturalLanguageAgentRequest(
                instruction="请改简历并生成面试准备，不要投递。",
                profile_id=profile.id,
                job_id=job.id,
            ),
        )
    )

    assert run.status == "completed"
    assert run.output_json["result_json"]["tailor"]["resume_version_id"] == 123
    assert run.output_json["result_json"]["interview_prep"]["interview_prep_id"] == 456
    assert "定制简历 #123" in run.output_json["user_message"]
    assert "面试包 #456" in run.output_json["user_message"]
    assert "#None" not in run.output_json["user_message"]
