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
from app.services.ops_audit import OpsAuditService
from app.services.agent_runtime import AgentErrorClassifier
from app.services.run_control import RunControlService
from app.services.stale_runs import StaleRunService
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
        priority = self._normalize_priority(str(payload.get("priority") or "normal"))
        queue_name = self.settings.redis_queue_names_by_priority[priority]
        payload = {**payload, "priority": priority, "enqueued_at": datetime.now(timezone.utc).isoformat()}
        self.redis.lpush(queue_name, json.dumps(payload, ensure_ascii=False, default=str))

    def _normalize_priority(self, priority: str) -> str:
        value = priority.lower().strip()
        return value if value in self.settings.redis_queue_names_by_priority else "normal"

    def _dead_letter_items(self, *, limit: int = 5) -> list[tuple[int, str, dict]]:
        raw_items = self.redis.lrange(self.settings.redis_dead_letter_queue_name, 0, max(limit - 1, 0)) or []
        items: list[tuple[int, str, dict]] = []
        for index, raw in enumerate(raw_items):
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            decoded = _safe_decode_queue_item(text)
            decoded["dlq_index"] = index
            if "dlq_id" not in decoded:
                decoded["dlq_id"] = f"legacy-index-{index}"
            items.append((index, text, decoded))
        return items

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

    def requeue_or_dead_letter(
        self,
        payload: dict,
        *,
        error: str,
        worker_id: str | None = None,
        error_envelope: dict | None = None,
    ) -> str:
        attempts = int(payload.get("attempts") or 0) + 1
        failed_payload = {
            **payload,
            "attempts": attempts,
            "last_error": error,
            "last_failed_at": datetime.now(timezone.utc).isoformat(),
            "worker_id": worker_id,
            "error_envelope": error_envelope or {},
        }
        retryable = True if error_envelope is None else bool(error_envelope.get("retryable"))
        if not retryable:
            failed_payload["terminal_reason"] = "non_retryable_error"
        if not retryable or attempts >= self.settings.redis_worker_max_attempts:
            failed_payload["dlq_id"] = failed_payload.get("dlq_id") or uuid4().hex
            failed_payload["dead_lettered_at"] = datetime.now(timezone.utc).isoformat()
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
            "priority_queues": self.settings.redis_queue_names_by_priority,
            "queued_by_priority": {
                priority: int(self.redis.llen(queue_name))
                for priority, queue_name in self.settings.redis_queue_names_by_priority.items()
            },
            "dead_letter_queue_name": self.settings.redis_dead_letter_queue_name,
            "queued_count": sum(
                int(self.redis.llen(queue_name)) for queue_name in self.settings.redis_queue_names_by_priority.values()
            ),
            "dead_letter_count": int(self.redis.llen(self.settings.redis_dead_letter_queue_name)),
            "dead_letter_preview": [decoded for _, _, decoded in self._dead_letter_items(limit=5)],
            "worker_max_attempts": self.settings.redis_worker_max_attempts,
            "queued_recovery_after_minutes": self.settings.redis_queued_recovery_after_minutes,
            "stale_recovery_after_minutes": self.settings.agent_run_stale_after_minutes,
            "max_crash_recovery_attempts": self.settings.agent_run_max_recovery_attempts,
        }

    def replay_dead_letter(self, db: Session, *, dlq_index: int, actor: str | None = None) -> dict:
        index, raw, decoded = self._get_dead_letter_item(dlq_index)
        removed = int(self.redis.lrem(self.settings.redis_dead_letter_queue_name, 1, raw) or 0)
        if removed < 1:
            raise ValueError(f"Dead-letter payload at index {index} was already changed or removed.")
        replay_payload = {
            key: value
            for key, value in decoded.items()
            if key
            not in {
                "dlq_index",
                "last_error",
                "last_failed_at",
                "dead_lettered_at",
                "worker_id",
            }
        }
        replay_payload["attempts"] = 0
        replay_payload["replayed_from_dlq_id"] = decoded.get("dlq_id")
        replay_payload["manual_replay_at"] = datetime.now(timezone.utc).isoformat()
        self.enqueue_payload(replay_payload)
        audit_payload = {"dlq_index": index, "payload": decoded, "replay_payload": replay_payload}
        OpsAuditService().record(
            db,
            event_type="dlq_payload_replayed",
            target_type="redis_dead_letter_queue",
            target_id=decoded.get("dlq_id"),
            actor=actor,
            payload=audit_payload,
        )
        self._trace_dlq_event(db, decoded, event_type="dlq_payload_replayed", event_payload=audit_payload)
        return {"status": "replayed", "dlq_index": index, "payload": decoded, "queued_payload": replay_payload}

    def discard_dead_letter(self, db: Session, *, dlq_index: int, actor: str | None = None) -> dict:
        index, raw, decoded = self._get_dead_letter_item(dlq_index)
        removed = int(self.redis.lrem(self.settings.redis_dead_letter_queue_name, 1, raw) or 0)
        if removed < 1:
            raise ValueError(f"Dead-letter payload at index {index} was already changed or removed.")
        audit_payload = {"dlq_index": index, "payload": decoded}
        OpsAuditService().record(
            db,
            event_type="dlq_payload_discarded",
            target_type="redis_dead_letter_queue",
            target_id=decoded.get("dlq_id"),
            actor=actor,
            payload=audit_payload,
        )
        self._trace_dlq_event(db, decoded, event_type="dlq_payload_discarded", event_payload=audit_payload)
        return {"status": "discarded", "dlq_index": index, "payload": decoded}

    def _get_dead_letter_item(self, dlq_index: int) -> tuple[int, str, dict]:
        if dlq_index < 0:
            raise ValueError("Dead-letter index must be non-negative.")
        items = self._dead_letter_items(limit=dlq_index + 1)
        if dlq_index >= len(items):
            raise ValueError(f"Dead-letter payload at index {dlq_index} was not found.")
        return items[dlq_index]

    def _trace_dlq_event(self, db: Session, original_payload: dict, *, event_type: str, event_payload: dict) -> None:
        run_id = original_payload.get("run_id")
        if not run_id:
            return
        try:
            parsed_run_id = int(run_id)
        except (TypeError, ValueError):
            return
        if db.query(AgentRun).filter(AgentRun.id == parsed_run_id).first() is None:
            return
        TraceService().add_event(db, run_id=parsed_run_id, event_type=event_type, payload=event_payload)

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

    def recover_stale_agent_runs(self, db: Session, *, older_than_minutes: int | None = None) -> list[dict]:
        stale_items = StaleRunService().find_stale(db, threshold_minutes=older_than_minutes)
        recovered: list[dict] = []
        trace = TraceService()
        control = RunControlService()
        for item in stale_items:
            run = db.query(AgentRun).filter(AgentRun.id == int(item["run_id"])).first()
            if run is None or run.status != "running":
                continue
            heartbeat_key = redis_key("career_agent", "runs", "heartbeat", run.id)
            lock_key = redis_key("career_agent", "runs", "lock", run.id)
            if self.redis.get(heartbeat_key) or self.redis.get(lock_key):
                continue
            input_json = dict(run.input_json or {})
            output_json = dict(run.output_json or {})
            execution_plan = input_json.get("execution_plan") or output_json.get("execution_plan") or {}
            graph_thread_id = (
                input_json.get("graph_thread_id")
                or output_json.get("graph_thread_id")
                or execution_plan.get("graph_thread_id")
            )
            if not graph_thread_id:
                run.status = "failed"
                run.error_message = "Agent run cannot be crash-recovered because graph_thread_id is missing."
                run.output_json = {
                    **output_json,
                    "error_type": "crash_recovery_unavailable",
                    "recovery_reason": "missing_graph_thread_id",
                    "last_stale_stage": item.get("last_stage"),
                }
                db.add(run)
                db.commit()
                db.refresh(run)
                payload = {
                    "reason": "missing_graph_thread_id",
                    "last_stage": item.get("last_stage"),
                    "last_event_at": item.get("last_event_at"),
                }
                trace.add_event(
                    db,
                    run_id=run.id,
                    event_type="crash_recovery_unavailable",
                    payload=payload,
                )
                control.create_action(
                    db,
                    run_id=run.id,
                    action_type="crash_recovery",
                    status="failed",
                    actor="redis_recovery_scanner",
                    payload=payload,
                )
                OpsAuditService().record(
                    db,
                    event_type="agent_run_crash_recovery_unavailable",
                    target_type="agent_run",
                    target_id=run.id,
                    actor="redis_recovery_scanner",
                    payload=payload,
                )
                continue
            recovery_attempt = int(input_json.get("recovery_attempt") or 0) + 1
            if recovery_attempt > self.settings.agent_run_max_recovery_attempts:
                run.status = "failed"
                run.error_message = (
                    "Agent run exceeded automatic crash recovery attempts: "
                    f"{self.settings.agent_run_max_recovery_attempts}."
                )
                run.output_json = {
                    **(run.output_json or {}),
                    "error_type": "crash_recovery_exhausted",
                    "recovery_attempt": recovery_attempt - 1,
                    "last_stale_stage": item.get("last_stage"),
                }
                db.add(run)
                db.commit()
                db.refresh(run)
                trace.add_event(
                    db,
                    run_id=run.id,
                    event_type="crash_recovery_exhausted",
                    payload={
                        "max_attempts": self.settings.agent_run_max_recovery_attempts,
                        "last_stage": item.get("last_stage"),
                        "last_event_at": item.get("last_event_at"),
                    },
                )
                control.create_action(
                    db,
                    run_id=run.id,
                    action_type="crash_recovery",
                    status="failed",
                    actor="redis_recovery_scanner",
                    payload={
                        "reason": "max_attempts_exceeded",
                        "max_attempts": self.settings.agent_run_max_recovery_attempts,
                    },
                )
                continue

            recovery_key = redis_key("career_agent", "runs", "crash_recovery", run.id, recovery_attempt)
            if not self.redis.set(recovery_key, "1", nx=True, ex=self.settings.redis_run_lock_ttl_seconds):
                continue
            recovered_at = datetime.now(timezone.utc).isoformat()
            run.input_json = {
                **input_json,
                "execution_mode": "checkpoint_resume",
                "recovery_attempt": recovery_attempt,
                "recovered_from_stage": item.get("last_stage"),
                "recovered_from_event_id": item.get("last_event_id"),
            }
            run.status = "queued"
            run.error_message = None
            run.output_json = {
                **(run.output_json or {}),
                "recovery": {
                    "status": "scheduled",
                    "attempt": recovery_attempt,
                    "last_stage": item.get("last_stage"),
                    "last_event_at": item.get("last_event_at"),
                    "scheduled_at": recovered_at,
                },
            }
            db.add(run)
            db.commit()
            db.refresh(run)
            self.enqueue_payload(
                {
                    "kind": "agent_run",
                    "run_id": run.id,
                    "attempts": 0,
                    "priority": "high",
                    "execution_mode": "checkpoint_resume",
                    "recovery_attempt": recovery_attempt,
                }
            )
            payload = {
                "run_id": run.id,
                "task_type": run.task_type,
                "recovery_attempt": recovery_attempt,
                "last_stage": item.get("last_stage"),
                "last_event_at": item.get("last_event_at"),
                "scheduled_at": recovered_at,
            }
            trace.add_event(db, run_id=run.id, event_type="crash_recovery_scheduled", payload=payload)
            control.create_action(
                db,
                run_id=run.id,
                action_type="crash_recovery",
                status="completed",
                actor="redis_recovery_scanner",
                payload={**payload, "stage": "scheduled"},
            )
            OpsAuditService().record(
                db,
                event_type="agent_run_crash_recovery_scheduled",
                target_type="agent_run",
                target_id=run.id,
                actor="redis_recovery_scanner",
                payload=payload,
            )
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
    try:
        item = runner.redis.brpop(settings.redis_priority_queue_names, timeout=timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        if _is_redis_socket_timeout(exc):
            return None
        raise
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
        error = ValueError(f"Unsupported queue payload kind: {kind}")
        envelope = AgentErrorClassifier().classify(error, step_name="redis_queue_decode").as_dict()
        runner.requeue_or_dead_letter(
            payload,
            error=str(error),
            worker_id=worker_id,
            error_envelope=envelope,
        )
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
            envelope = AgentErrorClassifier().classify(exc, step_name="task_worker").as_dict()
            runner.requeue_or_dead_letter(
                payload,
                error=f"{exc.__class__.__name__}: {exc}",
                worker_id=worker_id,
                error_envelope=envelope,
            )
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
            envelope = AgentErrorClassifier().classify(exc, step_name="agent_worker").as_dict()
            action = runner.requeue_or_dead_letter(
                payload,
                error=f"{exc.__class__.__name__}: {exc}",
                worker_id=worker_id,
                error_envelope=envelope,
            )
            try:
                TraceService().add_event(
                    db,
                    run_id=run_id,
                    event_type="worker_payload_failed",
                    payload={
                        "action": action,
                        "error": f"{exc.__class__.__name__}: {exc}",
                        "error_envelope": envelope,
                        "worker_id": worker_id,
                    },
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


def recover_stale_agent_runs_once(
    *,
    redis_client: RedisLike | None = None,
    settings: Settings | None = None,
    older_than_minutes: int | None = None,
) -> list[dict]:
    runner = RedisTaskRunner(redis_client=redis_client, settings=settings or get_settings())
    db = SessionLocal()
    try:
        return runner.recover_stale_agent_runs(db, older_than_minutes=older_than_minutes)
    finally:
        db.close()


def run_redis_worker_forever() -> None:
    settings = get_settings()
    next_recovery_at = 0.0
    while True:
        now = time.monotonic()
        if now >= next_recovery_at:
            recover_queued_agent_runs_once(settings=settings)
            recover_stale_agent_runs_once(settings=settings)
            next_recovery_at = now + settings.redis_worker_recovery_interval_seconds
        asyncio.run(consume_redis_queue_once(timeout_seconds=settings.redis_worker_poll_timeout_seconds))


def _is_redis_socket_timeout(exc: Exception) -> bool:
    return exc.__class__.__name__ == "TimeoutError" and exc.__class__.__module__.startswith("redis.")


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
