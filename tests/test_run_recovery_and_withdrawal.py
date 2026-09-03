import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.agents.orchestrator import AgentOrchestrator
from app.core.config import get_settings
from app.models.entities import (
    AgentApproval,
    AgentArtifact,
    AgentEvent,
    AgentRun,
    AgentRunControlAction,
    AgentStep,
    Application,
    InterviewPrep,
    Job,
    MatchResult,
    Profile,
    ResumeVersion,
)
from app.models.schemas import AgentRunRequest
from app.services.run_control import RunControlService, RunWithdrawalConflict
from app.services.task_runner import RedisTaskRunner


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}

    def set(self, name, value, nx=False, ex=None):
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    def get(self, name):
        return self.values.get(name)

    def delete(self, *names):
        for name in names:
            self.values.pop(name, None)
        return len(names)

    def lpush(self, name, value):
        self.lists.setdefault(name, []).insert(0, value)
        return len(self.lists[name])

    def llen(self, name):
        return len(self.lists.get(name) or [])

    def lrange(self, name, start, end):
        values = self.lists.get(name) or []
        return values[start:] if end == -1 else values[start : end + 1]

    def publish(self, channel, message):
        return 1


class IdempotentFakeMatcher:
    def create_match_result(self, db, profile, job, *, idempotency_key=None):
        if idempotency_key:
            existing = db.query(MatchResult).filter(MatchResult.idempotency_key == idempotency_key).first()
            if existing:
                return existing
        row = MatchResult(
            profile_id=profile.id,
            job_id=job.id,
            overall_score=88.0,
            dimension_scores_json={"required_skill_coverage": 100},
            matched_skills_json=["FastAPI", "RAG", "SQLite"],
            missing_skills_json=[],
            relevant_evidence_json=[],
            suggestions_json=[],
            idempotency_key=idempotency_key,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def retrieve_evidence_with_quality(self, db, profile_id, job, top_k=10):
        del db, profile_id, job, top_k
        return (
            [{"text": "Built CareerAgent with FastAPI, RAG and SQLite.", "chunk_type": "project"}],
            {"passed": True, "confidence": 1.0, "evidence_count": 1},
        )


class FlakyIdempotentTailor:
    def __init__(self, *, fail_once=False):
        self.fail_once = fail_once
        self.calls = 0

    async def tailor_resume(
        self,
        db,
        profile,
        job,
        *,
        idempotency_key=None,
        evidence=None,
        retrieval_quality=None,
    ):
        del retrieval_quality
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RuntimeError("simulated worker crash during tailor node")
        if idempotency_key:
            existing = db.query(ResumeVersion).filter(ResumeVersion.idempotency_key == idempotency_key).first()
            if existing:
                return existing
        row = ResumeVersion(
            profile_id=profile.id,
            job_id=job.id,
            title=f"{profile.name} - {job.title}",
            tailored_resume_markdown="CareerAgent: FastAPI, RAG, SQLite.",
            change_summary_json=[],
            keyword_alignment_json={},
            source_evidence_json=evidence or [],
            verification_json={"passed": True, "risk_level": "low"},
            diff_text=None,
            idempotency_key=idempotency_key,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def _profile_job(db_session):
    profile = Profile(
        name="Candidate",
        source_type="guided",
        raw_resume_text="Built CareerAgent with FastAPI, RAG and SQLite.",
        structured_profile_json={"skills": ["FastAPI", "RAG", "SQLite"]},
    )
    job = Job(
        source="manual",
        external_id=f"job-{uuid4().hex}",
        title="Agent 开发实习生",
        company="DemoAI",
        raw_jd_text="负责 Agent、FastAPI、RAG 和 SQLite。",
        structured_jd_json={"required_skills": ["FastAPI", "RAG", "SQLite"]},
    )
    db_session.add_all([profile, job])
    db_session.commit()
    db_session.refresh(profile)
    db_session.refresh(job)
    return profile, job


def _checkpoint_env(monkeypatch):
    path = Path(".tmp_test") / f"checkpoint_recovery_{uuid4().hex}.sqlite"
    path.parent.mkdir(exist_ok=True)
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_FILE", str(path.resolve()))
    get_settings.cache_clear()
    return path


def test_failed_node_can_resume_from_latest_checkpoint_without_replaying_completed_nodes(db_session, monkeypatch):
    checkpoint_path = _checkpoint_env(monkeypatch)
    profile, job = _profile_job(db_session)
    tailor = FlakyIdempotentTailor(fail_once=True)
    orchestrator = AgentOrchestrator(matcher=IdempotentFakeMatcher(), tailor=tailor)
    first = asyncio.run(
        orchestrator.run(
            db_session,
            AgentRunRequest(task_type="tailor_resume_for_job", profile_id=profile.id, job_id=job.id),
        )
    )
    assert first.status == "failed"

    first.status = "running"
    first.error_message = None
    first.input_json = {**first.input_json, "execution_mode": "checkpoint_resume", "recovery_attempt": 1}
    db_session.add(first)
    db_session.commit()
    resumed = asyncio.run(orchestrator.run_existing(db_session, first.id))

    assert resumed.status == "completed"
    assert resumed.output_json["recovery"]["mode"] == "checkpoint_resume"
    assert tailor.calls == 2
    assert db_session.query(AgentStep).filter(AgentStep.run_id == first.id, AgentStep.step_name == "plan_task").count() == 1
    assert db_session.query(MatchResult).count() == 1
    assert db_session.query(ResumeVersion).count() == 1
    event_types = [row.event_type for row in db_session.query(AgentEvent).filter(AgentEvent.run_id == first.id).all()]
    assert "checkpoint_recovery_loaded" in event_types
    checkpoint_path.unlink(missing_ok=True)
    get_settings.cache_clear()


def test_checkpoint_history_rewind_creates_isolated_run_and_thread(db_session, monkeypatch):
    checkpoint_path = _checkpoint_env(monkeypatch)
    profile, job = _profile_job(db_session)
    orchestrator = AgentOrchestrator(matcher=IdempotentFakeMatcher(), tailor=FlakyIdempotentTailor())
    source = asyncio.run(
        orchestrator.run(
            db_session,
            AgentRunRequest(task_type="tailor_resume_for_job", profile_id=profile.id, job_id=job.id),
        )
    )
    history = asyncio.run(orchestrator.checkpoint_history(source, limit=30))
    checkpoint = next(row for row in history if row["next_nodes"] == ["tailor_resume"])

    fork = asyncio.run(
        orchestrator.rewind_from_checkpoint(
            db_session,
            run_id=source.id,
            checkpoint_id=checkpoint["checkpoint_id"],
            actor="pytest",
            reason="验证历史分支",
        )
    )
    assert fork.status == "queued"
    assert fork.id != source.id
    assert fork.input_json["rewind_of_run_id"] == source.id
    assert fork.input_json["graph_thread_id"] != source.input_json["graph_thread_id"]

    completed = asyncio.run(orchestrator.run_existing(db_session, fork.id))
    assert completed.status == "completed"
    assert completed.output_json["recovery"]["mode"] == "checkpoint_rewind"
    assert db_session.query(ResumeVersion).count() == 2
    action = db_session.query(AgentRunControlAction).filter(AgentRunControlAction.run_id == source.id).one()
    assert action.status == "completed"
    assert action.target_run_id == fork.id
    checkpoint_path.unlink(missing_ok=True)
    get_settings.cache_clear()


def test_stale_running_run_is_requeued_only_without_heartbeat_or_lock(db_session):
    settings = get_settings()
    redis = FakeRedis()
    stale = AgentRun(
        task_type="tailor_resume_for_job",
        status="running",
        input_json={"graph_thread_id": "stale-thread"},
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    healthy = AgentRun(
        task_type="tailor_resume_for_job",
        status="running",
        input_json={"graph_thread_id": "healthy-thread"},
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add_all([stale, healthy])
    db_session.commit()
    db_session.refresh(stale)
    db_session.refresh(healthy)
    redis.set(f"career_agent:runs:heartbeat:{healthy.id}", "alive")

    recovered = RedisTaskRunner(redis_client=redis, settings=settings).recover_stale_agent_runs(
        db_session,
        older_than_minutes=0,
    )
    db_session.refresh(stale)
    db_session.refresh(healthy)
    assert [item["run_id"] for item in recovered] == [stale.id]
    assert stale.status == "queued"
    assert stale.input_json["execution_mode"] == "checkpoint_resume"
    assert stale.input_json["recovery_attempt"] == 1
    assert healthy.status == "running"
    assert redis.llen(settings.redis_high_priority_queue_name) == 1


def test_stale_legacy_run_without_graph_thread_is_failed_without_queue_retry(db_session):
    settings = get_settings()
    redis = FakeRedis()
    legacy = AgentRun(
        task_type="tailor_resume_for_job",
        status="running",
        input_json={},
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.refresh(legacy)

    recovered = RedisTaskRunner(redis_client=redis, settings=settings).recover_stale_agent_runs(
        db_session,
        older_than_minutes=0,
    )

    db_session.refresh(legacy)
    assert recovered == []
    assert legacy.status == "failed"
    assert legacy.output_json["error_type"] == "crash_recovery_unavailable"
    assert redis.llen(settings.redis_high_priority_queue_name) == 0
    action = db_session.query(AgentRunControlAction).filter(AgentRunControlAction.run_id == legacy.id).one()
    assert action.status == "failed"
    assert action.payload_json["reason"] == "missing_graph_thread_id"


def test_withdraw_soft_compensates_internal_artifacts_and_cancels_unexecuted_approval(db_session):
    profile, job = _profile_job(db_session)
    run = AgentRun(task_type="full_career_flow", status="completed", input_json={})
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    prefix = f"agent_run:{run.id}:"
    resume = ResumeVersion(
        profile_id=profile.id,
        job_id=job.id,
        title="定制简历",
        tailored_resume_markdown="CareerAgent",
        change_summary_json=[],
        keyword_alignment_json={},
        source_evidence_json=[],
        verification_json={"passed": True},
        idempotency_key=f"{prefix}resume:{profile.id}:{job.id}",
    )
    db_session.add(resume)
    db_session.commit()
    db_session.refresh(resume)
    application = Application(
        profile_id=profile.id,
        job_id=job.id,
        resume_version_id=resume.id,
        status="ready",
        checklist_json=[],
        idempotency_key=f"{prefix}application:{profile.id}:{job.id}:{resume.id}",
    )
    prep = InterviewPrep(
        profile_id=profile.id,
        job_id=job.id,
        title="面试包",
        summary_json={},
        question_sets_json=[],
        gap_drills_json=[],
        research_checklist_json=[],
        source_evidence_json=[],
        coverage_json={},
        idempotency_key=f"{prefix}interview_prep:{profile.id}:{job.id}",
    )
    approval = AgentApproval(
        run_id=run.id,
        action_type="email_send",
        status="approved",
        payload_hash="hash",
        payload_summary_json={"to": "hr@example.com"},
    )
    db_session.add_all([application, prep, approval])
    db_session.commit()

    withdrawn, action = RunControlService().withdraw(
        db_session,
        run=run,
        reason="选择了错误岗位",
        actor="pytest",
    )
    db_session.refresh(resume)
    db_session.refresh(application)
    db_session.refresh(prep)
    db_session.refresh(approval)
    assert withdrawn.status == "withdrawn"
    assert action.status == "completed"
    assert resume.lifecycle_status == "withdrawn"
    assert application.status == "withdrawn"
    assert prep.lifecycle_status == "withdrawn"
    assert approval.status == "cancelled"


def test_withdraw_refuses_to_hide_irreversible_external_side_effect(db_session):
    run = AgentRun(task_type="quick_apply", status="completed", input_json={})
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    db_session.add(
        AgentArtifact(
            run_id=run.id,
            artifact_type="email_send_result",
            artifact_json={
                "status": "tool_execution_completed",
                "approval_id": 9,
                "tool_result": {"status": "email_sent", "sent_at": datetime.now(timezone.utc).isoformat()},
            },
        )
    )
    db_session.commit()

    with pytest.raises(RunWithdrawalConflict) as exc_info:
        RunControlService().withdraw(db_session, run=run, reason="想撤回邮件", actor="pytest")
    assert exc_info.value.irreversible_actions[0]["artifact_type"] == "email_send_result"
    db_session.refresh(run)
    assert run.status == "completed"
    action = db_session.query(AgentRunControlAction).filter(AgentRunControlAction.run_id == run.id).one()
    assert action.status == "blocked"
