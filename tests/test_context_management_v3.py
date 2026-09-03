from __future__ import annotations

import asyncio
import time

import pytest

from app.core.llm import LLMClient, llm_trace_context
from app.models.entities import AgentArtifact, AgentRun, Job, LLMCallLog, Profile, ResumeChunk
from app.services.context_recovery import ContextRecoveryService
from app.services.context_runtime import (
    ContextIntegrityError,
    ContextJITLoader,
    ContextRequest,
    ContextRuntimeV2,
    ContextScope,
)
from app.services.conversation_compactor import ConversationCompactor
from app.services.document_schema_batcher import DocumentSchemaBatcher
from app.services.shared_context_batcher import SharedContextBatcher


class SummaryLLM:
    available = True

    def __init__(self, *, omit_context: bool = False) -> None:
        self.omit_context = omit_context
        self.calls = 0

    async def generate_json(self, **kwargs):
        self.calls += 1
        payload = __import__("json").loads(kwargs["user_prompt"])
        messages = payload["messages"]
        state = payload["current_task_state"]
        return {
            "discussion_summary": "" if self.omit_context else "讨论了 Agent 实习检索背景。",
            "rationales": [],
            "unresolved_questions": [],
            "source_message_ids": [item["message_id"] for item in messages],
            "task_state_version": state["version"],
            "task_state_claims": {
                "target_role": state["target_role"],
                "location": state["location"],
                "forbidden_actions": state["forbidden_actions"],
                "completed_actions": state["completed_actions"],
            },
            "historical_changes": [],
            "authoritative": False,
        }


def _messages() -> list[dict]:
    return [
        {
            "message_id": f"m{index}",
            "role": "user" if index % 2 else "assistant",
            "content": ("历史对话内容" * 80) + str(index),
            "critical_facts": ["只接受深圳岗位", "不要自动投递"] if index == 1 else [],
        }
        for index in range(1, 11)
    ]


def test_planner_compacts_old_conversation_and_preserves_critical_facts(db_session):
    run = AgentRun(
        tenant_id="tenant-a",
        user_id="user-a",
        task_type="natural_language_request",
        status="running",
        input_json={},
    )
    db_session.add(run)
    db_session.commit()
    result = asyncio.run(
        ConversationCompactor(SummaryLLM()).compact_if_needed(
            db_session,
            run_id=run.id,
            messages=_messages(),
            node_budget_tokens=1000,
            task_state={
                "target_role": "Agent 实习",
                "location": "深圳",
                "constraints": ["只接受深圳岗位"],
                "forbidden_actions": ["auto_apply"],
            },
        )
    )
    assert result.compactor_called is True
    assert len(result.recent_messages) == 6
    assert result.summary["task_state_claims"]["location"] == "深圳"
    assert result.summary["authoritative"] is False
    assert result.summary_artifact_id is not None


def test_conversation_summary_may_omit_business_constraint_when_task_state_is_authoritative(db_session):
    result = asyncio.run(
        ConversationCompactor(SummaryLLM(omit_context=True)).compact_if_needed(
            db_session,
            run_id=None,
            messages=_messages(),
            node_budget_tokens=1000,
            task_state={"location": "深圳", "forbidden_actions": ["auto_apply"]},
        )
    )
    assert result.summary["discussion_summary"] == ""
    assert result.summary["task_state_claims"]["location"] == "深圳"


def test_tailor_contract_drops_chat_and_raw_documents():
    result = ContextRuntimeV2().build(
        ContextRequest(
            run_id=1,
            node="resume_tailor",
            task_type="tailor_resume_for_job",
            scope=ContextScope("tenant-a", "user-a", 1),
            control={"policy": "grounded"},
            working={
                "profile": {"name": "Li", "skills": ["RAG"]},
                "job": {"title": "Agent 实习", "required_skills": ["RAG"]},
                "evidence": ["resume-p1"],
                "full_conversation": "不应进入",
                "raw_resume_text": "完整 PDF",
                "raw_jd_text": "完整 JD",
            },
        )
    )
    serialized = str(result.packet)
    assert "不应进入" not in serialized
    assert "完整 PDF" not in serialized
    assert "完整 JD" not in serialized


def test_jit_restores_citation_with_minimal_evidence_body(db_session):
    profile = Profile(
        tenant_id="tenant-a",
        name="Li",
        raw_resume_text="RAG project",
        structured_profile_json={"name": "Li", "skills": ["RAG"]},
    )
    db_session.add(profile)
    db_session.commit()
    chunk = ResumeChunk(
        profile_id=profile.id,
        chunk_uid="resume-p3-project",
        chunk_type="project",
        source="resume",
        text="CareerAgent 使用 BM25、向量检索和 RRF。",
        token_count=12,
        embedding_json=[0.1],
        metadata_json={"page_no": 3},
    )
    db_session.add(chunk)
    db_session.commit()
    scope = ContextScope("tenant-a", "user-a", profile.id)
    loader = ContextJITLoader(
        db_session,
        scope=scope,
        allowed_operations={"load_evidence_fragment"},
    )
    result = ContextRuntimeV2().build(
        ContextRequest(
            run_id=1,
            node="resume_tailor",
            task_type="tailor_resume_for_job",
            scope=scope,
            control={},
            working={"profile": profile.structured_profile_json, "job": {}, "evidence": []},
            evidence=[
                {
                    "citation_id": "resume-p3-project",
                    "evidence_type": "project",
                    "text": "",
                    "score": 1.0,
                }
            ],
            jit_loader=loader,
        )
    )
    evidence = result.packet["evidence_context"][0]
    assert evidence["jit_loaded"] is True
    assert "BM25" in evidence["text"]


def test_checkpoint_recovery_builds_next_node_minimal_context_and_receipts(db_session):
    profile = Profile(
        tenant_id="tenant-a",
        name="Li",
        raw_resume_text="Agent",
        structured_profile_json={"name": "Li", "skills": ["RAG"]},
    )
    job = Job(
        tenant_id="tenant-a",
        source="eval",
        external_id="job-1",
        title="Agent 实习",
        raw_jd_text="需要 RAG",
        structured_jd_json={"title": "Agent 实习", "required_skills": ["RAG"]},
    )
    db_session.add_all([profile, job])
    db_session.commit()
    run = AgentRun(
        tenant_id="tenant-a",
        user_id="user-a",
        task_type="tailor_resume_for_job",
        profile_id=profile.id,
        job_id=job.id,
        status="running",
        input_json={},
    )
    db_session.add(run)
    db_session.commit()
    artifact = AgentArtifact(
        run_id=run.id,
        artifact_type="email_send_result",
        artifact_json={"tool_result": {"status": "email_sent", "receipt_id": "send-1"}},
    )
    db_session.add(artifact)
    db_session.commit()
    recovered = ContextRecoveryService().rebuild_for_next_node(
        db_session,
        run=run,
        state={"profile_id": profile.id, "job_id": job.id, "query": "Agent"},
        next_node="match_job",
    )
    assert recovered.context_refs["profile_id"] == profile.id
    assert recovered.context_refs["job_id"] == job.id
    assert "send-1" in recovered.executed_side_effect_receipts
    assert ContextRecoveryService.side_effect_already_executed(
        recovered.context_refs, "send-1"
    )
    assert "raw_resume_text" not in str(recovered.packet)


def test_checkpoint_recovery_rejects_cross_tenant_profile(db_session):
    profile = Profile(
        tenant_id="tenant-b",
        name="Other",
        raw_resume_text="secret",
        structured_profile_json={"name": "Other"},
    )
    db_session.add(profile)
    db_session.commit()
    run = AgentRun(
        tenant_id="tenant-a",
        user_id="user-a",
        task_type="full_career_flow",
        status="running",
        input_json={},
    )
    db_session.add(run)
    db_session.commit()
    with pytest.raises(ContextIntegrityError, match="outside the Run tenant"):
        ContextRecoveryService().build_refs(
            db_session,
            run=run,
            state={"profile_id": profile.id},
        )


def test_three_questions_share_one_token_bounded_batch():
    batches = SharedContextBatcher().split(
        [{"question_id": f"q{index}", "question": "RAG 如何实现？"} for index in range(3)],
        shared_context={"profile": {"skills": ["RAG"]}, "job": {"title": "Agent"}},
        max_items=10,
        max_input_tokens=2000,
    )
    assert len(batches) == 1
    assert len(batches[0]) == 3


def test_document_batch_merge_keeps_conflict_provenance():
    batcher = DocumentSchemaBatcher()
    merged, provenance = batcher.merge(
        [
            ({"chunk_id": "page-1"}, {"name": "Li", "skills": ["RAG"]}),
            ({"chunk_id": "page-2"}, {"name": "Li Ming", "skills": ["FastAPI"]}),
        ],
        list_fields={"skills"},
    )
    assert merged["name"] == "Li"
    assert merged["skills"] == ["RAG", "FastAPI"]
    assert provenance["conflicts"][0]["chunk_id"] == "page-2"
    assert provenance["llm_conflict_resolution"] is False


def test_llm_log_keeps_run_node_business_batch_and_provider_usage(db_session):
    client = LLMClient()
    with llm_trace_context(run_id=17, graph_node="claim_verifier", batch_id="verify-q1-q3"):
        client._record_llm_call(
            db_session,
            trace_name="interview_agentic_rag.verify.1",
            status="completed",
            prompt_preview={"business_call_id": "business-9", "max_tokens": 600},
            response_preview="{}",
            error_message=None,
            started_at=time.perf_counter(),
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            provider_usage={"cached_tokens": 20, "reasoning_tokens": 0},
        )
    db_session.commit()
    row = db_session.query(LLMCallLog).one()
    assert row.context_json["run_id"] == 17
    assert row.context_json["node"] == "claim_verifier"
    assert row.context_json["business_call_id"] == "business-9"
    assert row.context_json["batch_id"] == "verify-q1-q3"
    assert row.context_json["usage_status"] == "provider_reported"
    assert row.total_tokens == 150
