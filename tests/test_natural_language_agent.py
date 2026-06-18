import asyncio
from types import SimpleNamespace

from app.agents.natural_language import NaturalLanguageAgentService
from app.models.entities import AgentEvent
from app.models.schemas import NaturalLanguageAgentRequest


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
