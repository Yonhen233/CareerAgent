from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agents.orchestrator import AgentOrchestrator
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.redis_client import RedisLike, get_redis_client, redis_key
from app.models.entities import AgentRun
from app.services.task_queue import TaskQueueService
from app.services.trace_service import TraceService


class TaskRunner(Protocol):
    def enqueue_agent_run(self, run_id: int) -> None: ...
    def enqueue_task_run(self, task_id: int) -> None: ...


@dataclass
class RedisRunLock:
    redis_client: RedisLike
    run_id: int
    worker_id: str
    ttl_seconds: int
    key_name: str | None = None

    @property
    def key(self) -> str:
        if self.key_name:
            return self.key_name
        return redis_key("career_agent", "runs", "lock", self.run_id)

    def acquire(self) -> bool:
        return bool(self.redis_client.set(self.key, self.worker_id, nx=True, ex=self.ttl_seconds))

    def release(self) -> bool:
        owner = self.redis_client.get(self.key)
        if owner != self.worker_id:
            return False
        self.redis_client.delete(self.key)
        return True


class RedisTaskRunner:
    def __init__(self, *, redis_client: RedisLike | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.redis = redis_client or get_redis_client()

    def enqueue_agent_run(self, run_id: int) -> None:
        self.enqueue_payload({"kind": "agent_run", "run_id": run_id, "attempts": 0})

    def enqueue_task_run(self, task_id: int) -> None:
        self.enqueue_payload({"kind": "task_run", "task_id": task_id, "attempts": 0})

    def enqueue_payload(self, payload: dict) -> None:
        payload = {**payload, "enqueued_at": datetime.now(timezone.utc).isoformat()}
        self.redis.lpush(self.settings.redis_queue_name, json.dumps(payload, ensure_ascii=False, default=str))

    def heartbeat(
        self,
        *,
        run_id: int,
        worker_id: str,
        stage: str = "running",
        kind: str = "agent_run",
        graph_thread_id: str | None = None,
        extra: dict | None = None,
    ) -> None:
        payload = json.dumps(
            {
                "worker_id": worker_id,
                "stage": stage,
                "kind": kind,
                "run_id": run_id,
                "graph_thread_id": graph_thread_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                **(extra or {}),
            },
            ensure_ascii=False,
        )
        key_parts = ("career_agent", "runs" if kind == "agent_run" else "tasks", "heartbeat", run_id)
        self.redis.set(
            redis_key(*key_parts),
            payload,
            ex=self.settings.redis_heartbeat_ttl_seconds,
        )

    def cancel_flag(self, run_id: int) -> None:
        self.redis.set(
            redis_key("career_agent", "runs", "cancel", run_id),
            "1",
            ex=self.settings.redis_run_lock_ttl_seconds,
        )

    def new_lock(self, run_id: int, worker_id: str | None = None) -> RedisRunLock:
        return RedisRunLock(
            redis_client=self.redis,
            run_id=run_id,
            worker_id=worker_id or f"worker-{uuid4().hex}",
            ttl_seconds=self.settings.redis_run_lock_ttl_seconds,
        )

    def requeue_or_dead_letter(self, payload: dict, *, error: str, worker_id: str | None = None) -> str:
        attempts = int(payload.get("attempts") or 0) + 1
        failed_payload = {
            **payload,
            "attempts": attempts,
            "last_error": error,
            "last_failed_at": datetime.now(timezone.utc).isoformat(),
            "worker_id": worker_id,
        }
        if attempts >= self.settings.redis_worker_max_attempts:
            self.redis.lpush(
                self.settings.redis_dead_letter_queue_name,
                json.dumps(failed_payload, ensure_ascii=False, default=str),
            )
            return "dead_lettered"
        self.enqueue_payload(failed_payload)
        return "requeued"

    def queue_status(self) -> dict:
        return {
            "redis_enabled": self.settings.redis_enabled,
            "queue_name": self.settings.redis_queue_name,
            "dead_letter_queue_name": self.settings.redis_dead_letter_queue_name,
            "queued_count": int(self.redis.llen(self.settings.redis_queue_name)),
            "dead_letter_count": int(self.redis.llen(self.settings.redis_dead_letter_queue_name)),
            "dead_letter_preview": [
                _safe_decode_queue_item(item)
                for item in (self.redis.lrange(self.settings.redis_dead_letter_queue_name, 0, 4) or [])
            ],
            "worker_max_attempts": self.settings.redis_worker_max_attempts,
            "queued_recovery_after_minutes": self.settings.redis_queued_recovery_after_minutes,
        }

    def recover_queued_agent_runs(self, db: Session, *, older_than_minutes: int | None = None) -> list[dict]:
        threshold_minutes = (
            self.settings.redis_queued_recovery_after_minutes if older_than_minutes is None else older_than_minutes
        )
        threshold = datetime.now(timezone.utc) - timedelta(minutes=threshold_minutes)
        rows = db.query(AgentRun).filter(AgentRun.status == "queued").order_by(AgentRun.created_at.asc()).all()
        recovered: list[dict] = []
        trace = TraceService()
        for run in rows:
            created_at = run.created_at if run.created_at.tzinfo else run.created_at.replace(tzinfo=timezone.utc)
            if threshold_minutes > 0 and created_at > threshold:
                continue
            recovery_key = redis_key("career_agent", "runs", "recovery", run.id)
            if not self.redis.set(recovery_key, "1", nx=True, ex=self.settings.redis_run_lock_ttl_seconds):
                continue
            self.enqueue_agent_run(run.id)
            payload = {
                "run_id": run.id,
                "task_type": run.task_type,
                "queued_since": created_at.isoformat(),
                "recovered_at": datetime.now(timezone.utc).isoformat(),
            }
            trace.add_event(db, run_id=run.id, event_type="queued_run_recovered", payload=payload)
            recovered.append(payload)
        return recovered


def get_task_runner() -> RedisTaskRunner:
    settings = get_settings()
    if not settings.redis_enabled:
        raise RuntimeError("Redis task queue is required for background Agent runs. Set REDIS_ENABLED=true.")
    return RedisTaskRunner(settings=settings)


async def execute_agent_run_once(run_id: int, *, db: Session | None = None) -> AgentRun:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        return await AgentOrchestrator().run_existing(session, run_id)
    finally:
        if owns_session:
            session.close()


async def consume_redis_queue_once(
    *,
    redis_client: RedisLike | None = None,
    settings: Settings | None = None,
    timeout_seconds: int = 5,
) -> AgentRun | None:
    settings = settings or get_settings()
    runner = RedisTaskRunner(redis_client=redis_client, settings=settings)
    item = runner.redis.brpop(settings.redis_queue_name, timeout=timeout_seconds)
    if not item:
        return None
    raw = item[1] if isinstance(item, tuple) else item
    worker_id = f"worker-{uuid4().hex}"
    try:
        payload = _decode_queue_item(raw)
    except Exception as exc:  # noqa: BLE001
        runner.redis.lpush(
            settings.redis_dead_letter_queue_name,
            json.dumps(
                {
                    "kind": "invalid_payload",
                    "raw": str(raw)[:2000],
                    "last_error": f"{exc.__class__.__name__}: {exc}",
                    "worker_id": worker_id,
                    "last_failed_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            ),
        )
        return None
    kind = str(payload.get("kind") or "agent_run")
    worker_id = f"worker-{uuid4().hex}"
    if kind not in {"agent_run", "task_run"}:
        runner.requeue_or_dead_letter(payload, error=f"Unsupported queue payload kind: {kind}", worker_id=worker_id)
        return None
    if kind == "task_run":
        task_id = int(payload["task_id"])
        lock = RedisRunLock(
            redis_client=runner.redis,
            run_id=task_id,
            worker_id=worker_id,
            ttl_seconds=settings.redis_run_lock_ttl_seconds,
            key_name=redis_key("career_agent", "tasks", "lock", task_id),
        )
        if not lock.acquire():
            return None
        runner.heartbeat(run_id=task_id, worker_id=worker_id, stage="task_lock_acquired", kind="task_run")
        try:
            runner.heartbeat(run_id=task_id, worker_id=worker_id, stage="llm_workflow_running", kind="task_run")
            await TaskQueueService().run_llm_workflow_task(task_id)
            runner.heartbeat(run_id=task_id, worker_id=worker_id, stage="task_completed", kind="task_run")
            return None
        except Exception as exc:  # noqa: BLE001
            runner.requeue_or_dead_letter(payload, error=f"{exc.__class__.__name__}: {exc}", worker_id=worker_id)
            return None
        finally:
            runner.redis.delete(redis_key("career_agent", "tasks", "heartbeat", task_id))
            lock.release()
    run_id = int(payload["run_id"])
    lock = runner.new_lock(run_id, worker_id=worker_id)
    if not lock.acquire():
        db = SessionLocal()
        try:
            if db.query(AgentRun).filter(AgentRun.id == run_id).first() is not None:
                TraceService().add_event(
                    db,
                    run_id=run_id,
                    event_type="run_lock_skipped",
                    payload={"worker_id": worker_id, "reason": "lock_not_acquired"},
                )
        finally:
            db.close()
        return None
    runner.heartbeat(run_id=run_id, worker_id=worker_id, stage="run_lock_acquired")
    try:
        db = SessionLocal()
        try:
            runner.heartbeat(run_id=run_id, worker_id=worker_id, stage="sqlite_run_loaded")
            run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
            if run is None:
                raise ValueError(f"Agent run {run_id} not found.")
            if run.status not in {"queued", "running"}:
                TraceService().add_event(
                    db,
                    run_id=run_id,
                    event_type="worker_skipped_run",
                    payload={"status": run.status, "worker_id": worker_id},
                )
                return run
            graph_thread_id = str((run.input_json or {}).get("graph_thread_id") or "")
            runner.heartbeat(
                run_id=run_id,
                worker_id=worker_id,
                stage="langgraph_starting",
                graph_thread_id=graph_thread_id,
            )
            result = await execute_agent_run_once(run_id, db=db)
            runner.heartbeat(
                run_id=run_id,
                worker_id=worker_id,
                stage=f"langgraph_finished:{result.status}",
                graph_thread_id=graph_thread_id,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            action = runner.requeue_or_dead_letter(payload, error=f"{exc.__class__.__name__}: {exc}", worker_id=worker_id)
            try:
                TraceService().add_event(
                    db,
                    run_id=run_id,
                    event_type="worker_payload_failed",
                    payload={"action": action, "error": f"{exc.__class__.__name__}: {exc}", "worker_id": worker_id},
                )
            except Exception:
                pass
            return None
        finally:
            db.close()
    finally:
        runner.redis.delete(redis_key("career_agent", "runs", "heartbeat", run_id))
        lock.release()


def recover_queued_agent_runs_once(
    *,
    redis_client: RedisLike | None = None,
    settings: Settings | None = None,
    older_than_minutes: int | None = None,
) -> list[dict]:
    runner = RedisTaskRunner(redis_client=redis_client, settings=settings or get_settings())
    db = SessionLocal()
    try:
        return runner.recover_queued_agent_runs(db, older_than_minutes=older_than_minutes)
    finally:
        db.close()


def run_redis_worker_forever() -> None:
    settings = get_settings()
    next_recovery_at = 0.0
    while True:
        now = time.monotonic()
        if now >= next_recovery_at:
            recover_queued_agent_runs_once(settings=settings)
            next_recovery_at = now + 60
        asyncio.run(consume_redis_queue_once(timeout_seconds=10))


def _decode_queue_item(item) -> dict:
    if isinstance(item, bytes):
        item = item.decode("utf-8")
    if isinstance(item, dict):
        return item
    return json.loads(str(item))


def _safe_decode_queue_item(item) -> dict:
    try:
        return _decode_queue_item(item)
    except Exception as exc:  # noqa: BLE001
        return {"raw": str(item)[:1000], "decode_error": f"{exc.__class__.__name__}: {exc}"}
