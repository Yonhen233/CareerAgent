from pathlib import Path
import sys


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.task_runner import run_redis_worker_forever


if __name__ == "__main__":
    run_redis_worker_forever()
