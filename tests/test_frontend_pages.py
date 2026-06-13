from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_frontend_pages_render():
    client = TestClient(app)
    for path in [
        "/",
        "/ui/profiles",
        "/ui/jobs",
        "/ui/agent-runs",
        "/ui/resumes",
        "/ui/applications",
        "/ui/interview-prep",
        "/ui/evaluations",
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert "CareerAgent" in response.text


def test_evaluations_page_exposes_interview_source_smoke_controls():
    client = TestClient(app)
    response = client.get("/ui/evaluations")

    assert response.status_code == 200
    assert "interview-source-smoke-form" in response.text
    assert "llm-workflow-form" in response.text
    assert "llm-workflow-result" in response.text
    assert "interview-source-import-form" in response.text
    assert "interview-source-import-result" in response.text
    assert "evaluation-runs-list" in response.text


def test_evaluations_frontend_exposes_llm_workflow_trace_panel():
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert "renderLLMWorkflow" in main_js
    assert "renderStageTrace" in main_js
    assert "renderCaseLLMLogs" in main_js
    assert "context_json?.case_name" in main_js
    assert "evaluation_run_id" in main_js
    assert "jd_parser.parse_jd.repair_json" in main_js
    assert "/evaluations/llm-workflow" in main_js
    assert "trace-list" in style_css
    assert "trace-step" in style_css


def test_interview_prep_frontend_exposes_question_quality_panel():
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert "renderQuestionQuality" in main_js
    assert "题目质量" in main_js
    assert "缺口边界" in main_js
    assert "失败项" in main_js
    assert "data-quality-jump" in main_js
    assert "focusInterviewQuestion" in main_js
    assert "data-question-id" in main_js
    assert "question-highlight" in style_css
