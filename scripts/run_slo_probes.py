from __future__ import annotations

import json
import asyncio

from fastapi.testclient import TestClient

from app.core.database import SessionLocal, init_db
from app.main import app
from app.agents.orchestrator import AgentOrchestrator
from app.models.entities import AgentRun, LLMCallLog
from app.models.schemas import AgentRunRequest
from app.services.slo_service import SLOService


def main() -> None:
    init_db()
    headers = {"x-careeragent-synthetic-probe": "1"}
    probes = ["/profiles", "/jobs", "/agent/runs"]
    with TestClient(app) as client:
        for _ in range(25):
            for path in probes:
                response = client.get(path, headers=headers)
                response.raise_for_status()
    db = SessionLocal()
    try:
        seed = (
            db.query(AgentRun)
            .filter(AgentRun.task_type == "find_jobs_for_profile", AgentRun.status == "completed")
            .order_by(AgentRun.id.desc())
            .first()
        )
        if seed is None:
            raise RuntimeError("A completed find_jobs_for_profile run is required as the probe seed.")
        allowed_fields = set(AgentRunRequest.model_fields)
        request_payload = {key: value for key, value in (seed.input_json or {}).items() if key in allowed_fields}
        request_payload["task_type"] = "find_jobs_for_profile"
        request = AgentRunRequest.model_validate(request_payload)
        initial_llm_calls = db.query(LLMCallLog).count()
        for _ in range(20):
            run = asyncio.run(AgentOrchestrator().run(db, request))
            run.input_json = {**(run.input_json or {}), "_traffic_class": "synthetic"}
            db.add(run)
            db.commit()
            if run.status != "completed":
                raise RuntimeError(f"Synthetic agent probe failed: run_id={run.id}, status={run.status}")
        final_llm_calls = db.query(LLMCallLog).count()
        if final_llm_calls != initial_llm_calls:
            raise RuntimeError("SLO probe unexpectedly called an LLM; results are invalid.")
        print(json.dumps(SLOService().report(db, window_days=7, traffic_class="synthetic"), ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
