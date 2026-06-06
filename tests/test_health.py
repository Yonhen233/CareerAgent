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


def test_agent_skills_and_subagents_endpoints_list_context_capabilities():
    client = TestClient(app)

    skills_response = client.get("/agent/skills")
    assert skills_response.status_code == 200
    skill_names = {item["name"] for item in skills_response.json()}
    assert "resume_tailoring" in skill_names
    assert "progressive_disclosure" not in skill_names

    subagents_response = client.get("/agent/subagents")
    assert subagents_response.status_code == 200
    subagent_names = {item["name"] for item in subagents_response.json()}
    assert "resume_writer" in subagent_names
    assert "context_manager" not in subagent_names
