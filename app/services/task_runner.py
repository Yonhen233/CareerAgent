from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
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
        payload = json.dumps({"kind": "agent_run", "run_id": run_id}, ensure_ascii=False)
        self.redis.lpush(self.settings.redis_queue_name, payload)

    def enqueue_task_run(self, task_id: int) -> None:
        payload = json.dumps({"kind": "task_run", "task_id": task_id}, ensure_ascii=False)
        self.redis.lpush(self.settings.redis_queue_name, payload)

    def heartbeat(self, *, run_id: int, worker_id: str, stage: str = "running") -> None:
        payload = json.dumps({"worker_id": worker_id, "stage": stage}, ensure_ascii=False)
        self.redis.set(
            redis_key("career_agent", "runs", "heartbeat", run_id),
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
    payload = json.loads(raw)
    kind = str(payload.get("kind") or "agent_run")
    if kind == "task_run":
        task_id = int(payload["task_id"])
        worker_id = f"worker-{uuid4().hex}"
        lock = RedisRunLock(
            redis_client=runner.redis,
            run_id=task_id,
            worker_id=worker_id,
            ttl_seconds=settings.redis_run_lock_ttl_seconds,
            key_name=redis_key("career_agent", "tasks", "lock", task_id),
        )
        if not lock.acquire():
            return None
        try:
            await TaskQueueService().run_llm_workflow_task(task_id)
            return None
        finally:
            lock.release()
    run_id = int(payload["run_id"])
    worker_id = f"worker-{uuid4().hex}"
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
    runner.heartbeat(run_id=run_id, worker_id=worker_id, stage="worker_started")
    try:
        db = SessionLocal()
        try:
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
            runner.heartbeat(run_id=run_id, worker_id=worker_id, stage="langgraph_running")
            return await execute_agent_run_once(run_id, db=db)
        finally:
            db.close()
    finally:
        runner.redis.delete(redis_key("career_agent", "runs", "heartbeat", run_id))
        lock.release()


def run_redis_worker_forever() -> None:
    while True:
        asyncio.run(consume_redis_queue_once(timeout_seconds=10))
