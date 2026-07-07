from __future__ import annotations

import multiprocessing as mp
import os
import signal
import time

from app.core.config import get_settings
from app.services.task_runner import run_redis_worker_forever


def _worker_main(index: int) -> None:
    os.environ["CAREER_AGENT_WORKER_INDEX"] = str(index)
    run_redis_worker_forever()


def main() -> None:
    settings = get_settings()
    worker_count = max(int(settings.redis_worker_concurrency), 1)
    processes: list[mp.Process] = []
    stopping = False

    def stop_workers(*_: object) -> None:
        nonlocal stopping
        stopping = True
        for process in processes:
            if process.is_alive():
                process.terminate()

    signal.signal(signal.SIGINT, stop_workers)
    signal.signal(signal.SIGTERM, stop_workers)

    for index in range(worker_count):
        process = mp.Process(target=_worker_main, args=(index,), name=f"career-agent-worker-{index}")
        process.start()
        processes.append(process)
        print(f"Started {process.name} pid={process.pid}", flush=True)

    while not stopping:
        for index, process in enumerate(list(processes)):
            if process.is_alive():
                continue
            exit_code = process.exitcode
            print(f"{process.name} exited with code={exit_code}", flush=True)
            if stopping:
                continue
            replacement = mp.Process(target=_worker_main, args=(index,), name=f"career-agent-worker-{index}")
            replacement.start()
            processes[index] = replacement
            print(f"Restarted {replacement.name} pid={replacement.pid}", flush=True)
        time.sleep(2)

    for process in processes:
        process.join(timeout=10)
        if process.is_alive():
            process.kill()


if __name__ == "__main__":
    main()
