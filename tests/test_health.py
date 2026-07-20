from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_agent_tools_endpoint_lists_registered_tools():
    client = TestClient(app)
    response = client.get("/agent/tools")

    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert "matcher.match_job" in names
    assert "vector_index.retrieve_resume_evidence" in names
    email_send = next(item for item in response.json() if item["name"] == "email_send")
    assert email_send["risk_level"] == "high"
    assert email_send["approval_requirement"] == "email_send"
    assert email_send["idempotency_policy"]
    assert "application_packet" in email_send["allowed_skills"]


def test_agent_skills_and_subagents_endpoints_list_context_capabilities():
    client = TestClient(app)

    skills_response = client.get("/agent/skills")
    assert skills_response.status_code == 200
    skill_names = {item["name"] for item in skills_response.json()}
    assert "resume_tailoring" in skill_names
    assert "progressive_disclosure" not in skill_names
    resume_tailoring = next(item for item in skills_response.json() if item["name"] == "resume_tailoring")
    assert resume_tailoring["version"] == "1.0.0"
    assert resume_tailoring["source_path"].endswith("resume_tailoring/SKILL.md")
    assert resume_tailoring["instructions_loaded"] is False
    assert "instructions" not in resume_tailoring

    detail_response = client.get("/agent/skills/resume_tailoring")
    assert detail_response.status_code == 200
    assert detail_response.json()["instructions_loaded"] is True
    assert "定制简历" in detail_response.json()["instructions"]
    assert detail_response.json()["forbidden_behaviors"]

    subagents_response = client.get("/agent/subagents")
    assert subagents_response.status_code == 200
    subagent_names = {item["name"] for item in subagents_response.json()}
    assert "resume_writer" in subagent_names
    assert "context_manager" not in subagent_names


def test_ops_readiness_and_metrics_endpoints():
    with TestClient(app) as client:
        readiness = client.get("/ops/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["checks"]["database"] == "ok"

        metrics = client.get("/ops/metrics")
        assert metrics.status_code == 200
        assert "request_count" in metrics.json()["app"]

        config = client.get("/ops/config")
        assert config.status_code == 200
        assert "api_key" not in str(config.json()).lower()


def test_tasks_endpoint_lists_task_runs():
    with TestClient(app) as client:
        response = client.get("/tasks")
        assert response.status_code == 200
        assert isinstance(response.json(), list)
