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
