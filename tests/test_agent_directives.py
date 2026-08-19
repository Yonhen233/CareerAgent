import asyncio

import pytest
from pydantic import ValidationError

from app.models.entities import AgentArtifact, AgentDirective, AgentEvent, AgentRun
from app.models.schemas import AgentDirectiveRequest
from app.services.agent_directives import AgentDirectiveService


class FakeNaturalLanguageAgent:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, db, request, *, tenant_id=None, user_id=None):
        self.requests.append(request)
        run = AgentRun(
            tenant_id=tenant_id,
            user_id=user_id,
            task_type="natural_language_request",
            profile_id=request.profile_id,
            job_id=request.job_id,
            status="completed",
            input_json=request.model_dump(),
            output_json={
                "status": "completed",
                "user_message": "已按追加要求创建新流程。",
                "result_json": {"updated": True},
            },
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run


def _source_run(db_session, *, status="completed"):
    run = AgentRun(
        tenant_id="tenant-a",
        user_id="user-a",
        task_type="tailor_resume_for_job",
        profile_id=None,
        job_id=None,
        status=status,
        input_json={
            "query": "Agent 开发实习",
            "location": "深圳",
            "limit": 12,
            "instruction": "为目标岗位定制简历",
            "graph_thread_id": "source-thread",
        },
        output_json={"resume_version_id": 91, "user_message": "定制简历已完成"},
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def test_follow_up_directive_creates_auditable_child_run(db_session):
    source = _source_run(db_session)
    fake = FakeNaturalLanguageAgent()
    service = AgentDirectiveService(natural_language=fake)

    directive = asyncio.run(
        service.append(
            db_session,
            source_run=source,
            payload=AgentDirectiveRequest(
                instruction="城市改为上海，并重新搜索岗位，不要生成投递材料。",
                selected_actions=["search_jobs"],
                client_request_id="request-12345678",
            ),
        )
    )

    assert directive.status == "completed"
    assert directive.target_run_id is not None
    target = db_session.query(AgentRun).filter(AgentRun.id == directive.target_run_id).one()
    assert target.id != source.id
    assert target.input_json["parent_run_id"] == source.id
    assert target.input_json["directive_id"] == directive.id
    assert target.input_json["conversation_root_run_id"] == source.id
    assert "只处理用户新增或修正的要求" in fake.requests[0].instruction
    assert "上海" in fake.requests[0].instruction
    assert fake.requests[0].profile_context["follow_up_source"]["source_run_id"] == source.id
    assert db_session.query(AgentArtifact).filter(
        AgentArtifact.run_id == target.id,
        AgentArtifact.artifact_type == "task_contract_revision",
    ).count() == 1
    event_types = {
        row.event_type
        for row in db_session.query(AgentEvent).filter(AgentEvent.run_id == source.id).all()
    }
    assert {"user_directive_received", "user_directive_completed"} <= event_types


def test_follow_up_directive_is_idempotent_per_source_run(db_session):
    source = _source_run(db_session)
    fake = FakeNaturalLanguageAgent()
    service = AgentDirectiveService(natural_language=fake)
    payload = AgentDirectiveRequest(
        instruction="补充生成面试准备，但不要重新修改简历。",
        client_request_id="request-idempotent-1",
    )

    first = asyncio.run(service.append(db_session, source_run=source, payload=payload))
    second = asyncio.run(service.append(db_session, source_run=source, payload=payload))

    assert first.id == second.id
    assert len(fake.requests) == 1
    assert db_session.query(AgentDirective).count() == 1


def test_follow_up_directive_rejects_hot_mutation_of_active_run(db_session):
    source = _source_run(db_session, status="running")
    service = AgentDirectiveService(natural_language=FakeNaturalLanguageAgent())

    with pytest.raises(ValueError, match="still active"):
        asyncio.run(
            service.append(
                db_session,
                source_run=source,
                payload=AgentDirectiveRequest(instruction="把城市改为北京。"),
            )
        )

    assert db_session.query(AgentDirective).count() == 0


def test_follow_up_directive_rejects_withdrawn_source_and_unknown_action(db_session):
    source = _source_run(db_session, status="withdrawn")
    service = AgentDirectiveService(natural_language=FakeNaturalLanguageAgent())

    with pytest.raises(ValueError, match="withdrawn"):
        asyncio.run(
            service.append(
                db_session,
                source_run=source,
                payload=AgentDirectiveRequest(instruction="重新搜索岗位。"),
            )
        )
    with pytest.raises(ValidationError):
        AgentDirectiveRequest(
            instruction="执行未知动作。",
            selected_actions=["delete_database"],
        )


def test_follow_up_idempotency_key_is_bound_to_normalized_payload(db_session):
    source = _source_run(db_session)
    fake = FakeNaturalLanguageAgent()
    service = AgentDirectiveService(natural_language=fake)

    asyncio.run(
        service.append(
            db_session,
            source_run=source,
            payload=AgentDirectiveRequest(
                instruction="重新搜索上海岗位。",
                selected_actions=["search_jobs", "search_jobs"],
                client_request_id="request-payload-key",
            ),
        )
    )
    with pytest.raises(ValueError, match="different follow-up payload"):
        asyncio.run(
            service.append(
                db_session,
                source_run=source,
                payload=AgentDirectiveRequest(
                    instruction="重新搜索北京岗位。",
                    selected_actions=["search_jobs"],
                    client_request_id="request-payload-key",
                ),
            )
        )

    assert len(fake.requests) == 1


def test_follow_up_tolerates_malformed_legacy_context_and_limit(db_session):
    source = _source_run(db_session)
    source.input_json = {**source.input_json, "limit": "legacy-invalid"}
    source.output_json = {
        **source.output_json,
        "selected_job": "broken",
        "application": "broken",
        "interview_prep": ["broken"],
    }
    db_session.add(source)
    db_session.commit()
    fake = FakeNaturalLanguageAgent()

    directive = asyncio.run(
        AgentDirectiveService(natural_language=fake).append(
            db_session,
            source_run=source,
            payload=AgentDirectiveRequest(
                instruction="只重新搜索岗位。",
                client_request_id="request-legacy-context",
            ),
        )
    )

    assert directive.context_json["selected_job"] == {}
    assert directive.context_json["available_artifact_ids"]["application_id"] is None
    assert fake.requests[0].limit == 8
