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
        "/ui/prep",
        "/ui/evaluations",
        "/ui/quality",
        "/ui/ops",
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
    assert "llm-workflow-task-form" in response.text
    assert "task-runs-list" in response.text
    assert "interview-source-import-form" in response.text
    assert "interview-source-import-result" in response.text
    assert "evaluation-runs-list" in response.text


def test_dashboard_exposes_user_start_flow_and_console_entry():
    client = TestClient(app)
    response = client.get("/")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "natural-language-form" in response.text
    assert "natural-language-result" in response.text
    assert "让 Agent 自动处理" in response.text
    assert "career-start-form" in response.text
    assert "career-flow-steps" in response.text
    assert "career-flow-result" in response.text
    assert "name=\"job_id\"" in response.text
    assert "name=\"jd_text\"" in response.text
    assert "一键运行" in response.text
    assert "控制台" in response.text
    assert ">运维<" not in response.text
    assert "dashboard-ops-summary" not in response.text
    assert "runCareerStartFlow" in main_js
    assert "runNaturalLanguageRequest" in main_js
    assert "/assistant/natural-language" in main_js
    assert "renderNaturalLanguageResult" in main_js
    assert "createProfileForCareerFlow" in main_js
    assert "createAgentRun" in main_js
    assert "run.status !== \"completed\"" in main_js
    assert "resolveDirectJobForCareerFlow" in main_js
    assert "/matches" in main_js
    assert "task_type: \"find_jobs_for_profile\"" in main_js
    assert "flow-stepper" in style_css
    assert "console-entry" in style_css


def test_resume_pages_expose_html_preview_controls():
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert "/profiles/${row.id}/html" in main_js
    assert "预览简历" in main_js
    assert "/resumes/${row.id}/html" in main_js
    assert "resume-preview-frame" in main_js
    assert "打开 HTML 预览" in main_js
    assert "下载 Markdown" in main_js
    assert "resume-preview-frame" in style_css


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
    assert "/tasks/llm-workflow" in main_js
    assert "renderTaskRun" in main_js
    assert "trace-list" in style_css
    assert "trace-step" in style_css
    assert "progress-bar" in style_css


def test_ops_frontend_exposes_production_controls():
    client = TestClient(app)
    response = client.get("/ui/ops")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "ops-readiness" in response.text
    assert "admin-token-form" in response.text
    assert "ops-metrics" in response.text
    assert "ops-llm-logs" in response.text
    assert "/ui/quality" in response.text
    assert "/docs" in response.text
    assert "loadOpsPage" in main_js
    assert "careeragent.admin_token" in main_js
    assert "X-Admin-Token" in main_js
    assert "loadDashboardOpsSummary" in main_js
    assert "details-block" in style_css


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
