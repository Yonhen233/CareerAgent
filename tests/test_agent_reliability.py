import asyncio

import pytest

from app.models.entities import AgentArtifact, AgentRun, AgentStep, Profile
from app.models.schemas import AgentRunRequest
from app.agents.orchestrator import AgentOrchestrator
from app.agents.tools import bind_agent_tool
from app.services.agent_reliability import (
    AgentExecutionBudgetExceeded,
    AgentTaskContractService,
    AgentTrajectoryEvaluator,
)
from app.services.retrieval_quality import RetrievalQualityService
from app.services.trace_service import TraceService


def test_trajectory_v2_rejects_wrong_arguments_order_and_unexpected_tool(db_session):
    run = AgentRun(
        task_type="tailor_resume_for_job",
        profile_id=7,
        job_id=9,
        status="completed",
        input_json={"profile_id": 7, "job_id": 9},
        output_json={},
    )
    db_session.add(run)
    db_session.commit()
    steps = [
        ("plan_task", "LangGraph.AgentPlanner", {"task_type": "tailor_resume_for_job"}),
        ("load_job", "job_repository.load_job", {"job_id": 9}),
        ("load_profile", "profile_repository.load_profile", {"profile_id": 999}),
        ("match_job", "matcher.match_job", {"profile_id": 7, "job_id": 9}),
        ("tailor_resume_with_rag", "resume_tailor.tailor_resume", {"profile_id": 7, "job_id": 9}),
        ("send_without_approval", "email.send", {"profile_id": 7, "job_id": 9}),
    ]
    for step_name, tool_name, payload in steps:
        db_session.add(
            AgentStep(
                run_id=run.id,
                step_name=step_name,
                tool_name=tool_name,
                status="completed",
                input_json=payload,
            )
        )
    db_session.add(AgentArtifact(run_id=run.id, artifact_type="completion_verification", artifact_json={}))
    db_session.commit()

    report = AgentTrajectoryEvaluator().evaluate(
        db_session,
        run_id=run.id,
        task_type=run.task_type,
        request=run.input_json,
    )

    assert report["passed"] is False
    assert report["argument_violations"]
    assert report["order_violations"]
    assert report["unexpected_tools"] == ["email.send"]


def test_trace_budget_rejects_third_identical_tool_call(db_session):
    trace = TraceService()
    run = trace.create_run(db_session, task_type="natural_language_request", input_json={})

    async def invoke():
        return {"task_type": "same", "steps": []}

    asyncio.run(
        trace.step(
            db_session,
            run_id=run.id,
            step_name="first",
            input_json={"task_type": "same"},
            tool=bind_agent_tool("LangGraph.AgentPlanner", invoke),
        )
    )
    asyncio.run(
        trace.step(
            db_session,
            run_id=run.id,
            step_name="second",
            input_json={"task_type": "same"},
            tool=bind_agent_tool("LangGraph.AgentPlanner", invoke),
        )
    )
    with pytest.raises(AgentExecutionBudgetExceeded):
        asyncio.run(
            trace.step(
                db_session,
                run_id=run.id,
                step_name="third",
                input_json={"task_type": "same"},
                tool=bind_agent_tool("LangGraph.AgentPlanner", invoke),
            )
        )


def test_retrieval_quality_distinguishes_supported_and_insufficient_evidence():
    service = RetrievalQualityService()
    supported = [
        {
            "text": "CareerAgent 使用 FastAPI 和 RAG 构建岗位匹配流程",
            "chunk_type": "project",
            "score": 0.8,
            "metadata": {"retrieval": {"first_stage_score": 0.6}},
        },
        {
            "text": "实现 SQLite 元数据存储和 LangGraph 编排",
            "chunk_type": "skill",
            "score": 0.7,
            "metadata": {"retrieval": {"first_stage_score": 0.5}},
        },
    ]
    noise = [
        {
            "text": "摄影社团活动和校园宣传",
            "chunk_type": "other",
            "score": 0.01,
            "metadata": {"retrieval": {"first_stage_score": 0.01}},
        }
    ]

    good = service.assess("Agent FastAPI RAG SQLite LangGraph", supported)
    bad = service.assess("Agent FastAPI RAG SQLite LangGraph", noise)

    assert good["passed"] is True
    assert bad["passed"] is False
    assert bad["downstream_policy"] == "allow_gap_detection_but_block_evidence-dependent_generation"


def test_retrieval_quality_deduplicates_evidence_before_counting_support():
    duplicate = {
        "text": "CareerAgent 使用 FastAPI 和 RAG 构建岗位匹配流程",
        "chunk_type": "project",
        "score": 0.8,
        "metadata": {"retrieval": {"first_stage_score": 0.6, "lexical_score": 0.5}},
    }
    report = RetrievalQualityService().assess(
        "Agent FastAPI RAG",
        [duplicate, dict(duplicate)],
        expected_chunk_types={"project", "experience"},
        min_evidence_chunks=2,
    )

    assert report["passed"] is False
    assert report["evidence_count"] == 2
    assert report["unique_evidence_count"] == 1
    assert report["duplicate_evidence_count"] == 1


def test_retrieval_quality_rejects_wrong_semantic_chunk_type_even_with_high_score():
    report = RetrievalQualityService().assess(
        "Agent FastAPI RAG",
        [
            {
                "text": "Agent FastAPI RAG",
                "chunk_type": "other",
                "score": 0.99,
                "metadata": {"retrieval": {"first_stage_score": 0.95, "lexical_score": 1.0}},
            }
        ],
        expected_chunk_types={"project", "experience", "skill"},
    )

    assert report["passed"] is False
    assert report["expected_type_coverage"] == 0
    assert "no expected semantic chunk type was retrieved" in report["reasons"]


def test_retrieval_quality_does_not_count_negative_or_planned_chunks_as_generation_support():
    report = RetrievalQualityService().assess(
        "投递前需要人工审批，并使用 LangGraph checkpoint 恢复",
        [
            {
                "text": "邮件已经发送后才显示确认提示，没有实现投递前人工审批。",
                "chunk_type": "project",
                "score": 0.95,
                "metadata": {
                    "retrieval": {
                        "vector_score": 0.88,
                        "first_stage_score": 0.80,
                        "lexical_score": 0.50,
                    }
                },
            },
            {
                "text": "计划学习 LangGraph checkpoint，目前没有实现恢复。",
                "chunk_type": "project",
                "score": 0.90,
                "metadata": {
                    "retrieval": {
                        "vector_score": 0.82,
                        "first_stage_score": 0.76,
                        "lexical_score": 0.42,
                    }
                },
            },
        ],
        expected_chunk_types={"project", "experience"},
        min_evidence_chunks=2,
        require_supportive_evidence=True,
    )

    assert report["passed"] is False
    assert report["supporting_evidence_count"] == 0
    assert report["blocked_weak_evidence_count"] == 2


def test_retrieval_quality_uses_provider_specific_hash_thresholds_without_lowering_production_gate():
    report = RetrievalQualityService().assess(
        "Recommendation Ranking Metrics",
        [
            {
                "text": "Built experiment dashboards and analyzed A/B tests, but did not implement ranking models.",
                "chunk_type": "project",
                "score": 0.56,
                "metadata": {
                    "retrieval": {
                        "query_embedding": {"provider": "hash"},
                        "vector_score": 0.29,
                        "first_stage_score": 0.31,
                        "lexical_score": 0.25,
                    }
                },
            },
            {
                "text": "Metrics",
                "chunk_type": "skill",
                "score": 0.44,
                "metadata": {
                    "retrieval": {
                        "query_embedding": {"provider": "hash"},
                        "vector_score": 0.31,
                        "first_stage_score": 0.21,
                        "lexical_score": 0.05,
                    }
                },
            },
        ],
        expected_chunk_types={"project", "experience", "skill"},
        min_evidence_chunks=2,
        require_supportive_evidence=True,
    )

    assert report["passed"] is True
    assert report["embedding_providers"] == ["hash"]
    assert report["thresholds"]["min_vector_score"] == 0.5


def test_natural_language_completion_gate_detects_silent_early_stop():
    report = AgentTaskContractService().verify_natural_language(
        plan={
            "intent": "search_jobs",
            "actions": ["search_jobs", "tailor_resume", "interview_prep"],
        },
        result={"matches": [{"job_id": 10}]},
    )

    assert report["passed"] is False
    assert report["missing_actions"] == ["tailor_resume", "interview_prep"]
    assert report["terminal_decision"] == "repair"


def test_natural_language_completion_gate_rejects_cross_job_result():
    report = AgentTaskContractService().verify_natural_language(
        plan={"intent": "tailor_resume", "actions": ["tailor_resume"]},
        request={"profile_id": 7, "job_id": 9},
        result={
            "profile": {"id": 7},
            "job": {"id": 10},
            "tailor": {"resume_version_id": 20, "profile_id": 7, "job_id": 10},
            "agent_runs": [{"run_id": 3, "status": "completed"}],
        },
    )

    assert report["passed"] is False
    assert report["missing_actions"] == []
    assert report["integrity_violations"][0]["section"] == "job"


def test_database_integrity_rejects_nonexistent_state_artifacts(db_session):
    violations = AgentTaskContractService()._database_integrity_violations(
        db_session,
        task_type="tailor_resume_for_job",
        state={
            "profile_id": 98701,
            "job_id": 98702,
            "match_result_id": 98703,
            "resume_version_id": 98704,
        },
    )

    assert {item["entity"] for item in violations} == {
        "profile",
        "job",
        "match_result",
        "resume_version",
    }


def test_trace_budget_rejects_changed_inputs_with_identical_outputs(db_session):
    trace = TraceService()
    run = trace.create_run(db_session, task_type="natural_language_request", input_json={})

    async def invoke():
        return {"task_type": "search_jobs", "steps": []}

    for index in range(2):
        asyncio.run(
            trace.step(
                db_session,
                run_id=run.id,
                step_name=f"planner_{index}",
                input_json={"task_type": f"search_{index}"},
                tool=bind_agent_tool("LangGraph.AgentPlanner", invoke),
            )
        )
    with pytest.raises(AgentExecutionBudgetExceeded, match="without observable progress"):
        asyncio.run(
            trace.step(
                db_session,
                run_id=run.id,
                step_name="planner_2",
                input_json={"task_type": "search_2"},
                tool=bind_agent_tool("LangGraph.AgentPlanner", invoke),
            )
        )


def test_completion_integrity_rejects_cross_job_artifact_mixup():
    state = {
        "profile_id": 1,
        "job_id": 10,
        "resume_version_id": 20,
        "selected_job": {"job_id": 10},
        "matches": [{"job_id": 10}],
        "tailor": {"profile_id": 1, "job_id": 99, "resume_version_id": 20},
        "application": {
            "application_id": 30,
            "profile_id": 1,
            "job_id": 10,
            "resume_version_id": 20,
        },
        "interview_prep": {"interview_prep_id": 40, "profile_id": 1, "job_id": 10},
        "output": {},
    }

    violations = AgentTaskContractService()._state_integrity_violations("full_career_flow", state)

    assert violations == [
        {"section": "tailor", "field": "job_id", "actual": 99, "expected": 10}
    ]


def test_empty_job_search_cannot_claim_task_completed(db_session):
    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Built an Agent project.",
        structured_profile_json={"skills": ["Agent"]},
    )
    db_session.add(profile)
    db_session.commit()

    class EmptySearch:
        async def search(self, db, **kwargs):
            return [], {}

    run = asyncio.run(
        AgentOrchestrator(job_search=EmptySearch()).run(
            db_session,
            AgentRunRequest(
                task_type="find_jobs_for_profile",
                profile_id=profile.id,
                query="Agent 实习",
            ),
        )
    )

    assert run.status == "failed"
    report = (
        db_session.query(AgentArtifact)
        .filter(AgentArtifact.run_id == run.id, AgentArtifact.artifact_type == "completion_verification")
        .one()
        .artifact_json
    )
    assert report["passed"] is False
    assert {"jobs_retrieved", "jobs_ranked", "result_exposed"} <= set(report["missing_goals"])
