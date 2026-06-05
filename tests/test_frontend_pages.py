from fastapi.testclient import TestClient

from app.main import app


def test_frontend_pages_render():
    client = TestClient(app)
    for path in ["/", "/ui/profiles", "/ui/jobs", "/ui/agent-runs", "/ui/resumes", "/ui/applications"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "CareerAgent" in response.text
