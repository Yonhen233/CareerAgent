from __future__ import annotations

import multiprocessing as mp
import os
import json
import signal
import time
from datetime import datetime, timezone

from app.core.config import get_settings
from app.services.task_runner import run_redis_worker_forever


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event: str, **payload: object) -> None:
    settings = get_settings()
    record = {"ts": _now(), "event": event, **payload}
    if settings.supervisor_log_json:
        print(json.dumps(record, ensure_ascii=False), flush=True)
    else:
        print(f"{record}", flush=True)


def _worker_main(index: int) -> None:
    os.environ["CAREER_AGENT_WORKER_INDEX"] = str(index)
    run_redis_worker_forever()


def _write_health(processes: list[mp.Process], *, state: str) -> None:
    settings = get_settings()
    settings.supervisor_health_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "updated_at": _now(),
        "worker_count": len(processes),
        "alive_count": sum(1 for process in processes if process.is_alive()),
        "workers": [
            {
                "name": process.name,
                "pid": process.pid,
                "alive": process.is_alive(),
                "exitcode": process.exitcode,
            }
            for process in processes
        ],
    }
    settings.supervisor_health_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    settings = get_settings()
    worker_count = max(int(settings.redis_worker_concurrency), 1)
    processes: list[mp.Process] = []
    stopping = False

    def stop_workers(*_: object) -> None:
        nonlocal stopping
        stopping = True
        _log("supervisor_stopping", reason="signal")
        for process in processes:
            if process.is_alive():
                process.terminate()

    signal.signal(signal.SIGINT, stop_workers)
    signal.signal(signal.SIGTERM, stop_workers)

    for index in range(worker_count):
        process = mp.Process(target=_worker_main, args=(index,), name=f"career-agent-worker-{index}")
        process.start()
        processes.append(process)
        _log("worker_started", name=process.name, pid=process.pid)

    while not stopping:
        if settings.supervisor_drain_path.exists():
            stopping = True
            _log("supervisor_drain_requested", drain_file=str(settings.supervisor_drain_path))
            for process in processes:
                if process.is_alive():
                    process.terminate()
            break
        for index, process in enumerate(list(processes)):
            if process.is_alive():
                continue
            exit_code = process.exitcode
            _log("worker_exited", name=process.name, pid=process.pid, exit_code=exit_code)
            if stopping:
                continue
            replacement = mp.Process(target=_worker_main, args=(index,), name=f"career-agent-worker-{index}")
            replacement.start()
            processes[index] = replacement
            _log("worker_restarted", name=replacement.name, pid=replacement.pid)
        _write_health(processes, state="running")
        time.sleep(2)

    for process in processes:
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            _log("worker_killed", name=process.name, pid=process.pid)
    _write_health(processes, state="stopped")
    _log("supervisor_stopped")


if __name__ == "__main__":
    main()
