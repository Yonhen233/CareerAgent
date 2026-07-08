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
        "/ui/outbound-smoke",
        "/ui/outbound-smoke/target",
    ]:
        response = client.get(path)
        assert response.status_code == 200
        assert "CareerAgent" in response.text


def test_ops_console_exposes_queue_approvals_cancel_and_stale_controls():
    client = TestClient(app)
    response = client.get("/ui/ops")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "ops-queue" in response.text
    assert "ops-agent-runs" in response.text
    assert "ops-approvals" in response.text
    assert "ops-stale-runs" in response.text
    assert "ops-audit-events" in response.text
    assert "/ui/outbound-smoke" in response.text
    assert "recover-queued-runs" in response.text
    assert "mark-stale-runs" in response.text
    assert "/ops/queue/status" in main_js
    assert "/ops/queue/recover-queued" in main_js
    assert "/ops/queue/dead-letter/${index}/replay" in main_js
    assert "/ops/queue/dead-letter/${index}/discard" in main_js
    assert "/ops/approvals?limit=20" in main_js
    assert "/ops/audit-events?limit=20" in main_js
    assert "/ops/agent-runs/stale" in main_js
    assert "/cancel" in main_js
    assert "data-approval-decision" in main_js
    assert "data-dlq-replay" in main_js
    assert "data-dlq-discard" in main_js


def test_outbound_smoke_pages_expose_browser_and_smtp_payloads():
    client = TestClient(app)
    smoke = client.get("/ui/outbound-smoke")
    target = client.get("/ui/outbound-smoke/target")

    assert smoke.status_code == 200
    assert "browser_apply" in smoke.text
    assert "email_draft" in smoke.text
    assert "email_send" in smoke.text
    assert "#full_name" in smoke.text
    assert target.status_code == 200
    assert 'id="full_name"' in target.text
    assert 'id="email"' in target.text
    assert 'id="message"' in target.text


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
    assert "告诉 CareerAgent 你的求职目标" in response.text
    assert "你想让 Agent 做什么" in response.text
    assert "信息会自动合并" in response.text
    assert "生成内容" in response.text
    assert "选择 PDF 后会自动解析并回填表单" in response.text
    for action in ["create_profile", "search_jobs", "tailor_resume", "quick_apply", "interview_prep"]:
        assert f'value="{action}"' in response.text
    assert "需求入口" not in response.text
    assert "一键开始" not in response.text
    assert "career-start-form" in response.text
    assert "career-flow-steps" in response.text
    assert "career-flow-result" in response.text
    assert "name=\"job_id\"" in response.text
    assert "name=\"jd_text\"" in response.text
    assert "开始处理" in response.text
    assert "控制台" in response.text
    assert ">运维<" not in response.text
    assert "dashboard-ops-summary" not in response.text
    assert "runCareerStartFlow" in main_js
    assert "runNaturalLanguageRequest" in main_js
    assert "updateCareerFlowFromNaturalResult" in main_js
    assert "packageLabel" in main_js
    assert "parseResumeFileIntoStartForm" in main_js
    assert "populateStartFormFromProfile" in main_js
    assert "profileContextFromStartForm" in main_js
    assert "selected_actions: actions" in main_js
    assert "profile_context: hasProfileContext(profileContext)" in main_js
    assert "pushUniqueAction" in main_js
    assert "runActionKeys" in main_js
    assert "tailor_resume_for_job: \"resumes\"" in main_js
    assert "prepare_interview_for_job: \"prep\"" in main_js
    assert "/assistant/natural-language" in main_js
    assert "renderNaturalLanguageResult" in main_js
    assert "createProfileForCareerFlow" in main_js
    assert "createAgentRun" in main_js
    assert "createBackgroundAgentRun" in main_js
    assert "waitForAgentRun" in main_js
    assert "EventSource" in main_js
    assert "/agent/runs/background" in main_js
    assert "/events/stream" in main_js
    assert "task_type: \"full_career_flow\"" in main_js
    assert "run.status !== \"completed\"" in main_js
    assert "resolveDirectJobForCareerFlow" in main_js
    assert "/matches" in main_js
    assert "task_type: \"find_jobs_for_profile\"" in main_js
    assert "flow-stepper" in style_css
    assert "generation-picker" in style_css
    assert "guidance-card" in style_css
    assert "console-entry" in style_css


def test_applications_page_uses_package_number_and_constrained_layout():
    client = TestClient(app)
    response = client.get("/ui/applications")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "我的投递材料" in response.text
    assert "userPackageId(row)" in main_js
    assert "求职包" in main_js
    assert "application-card" in main_js
    assert "application-letter" in main_js
    assert ".application-card" in style_css
    assert ".application-letter" in style_css
    assert "overflow-wrap: anywhere" in style_css


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


def test_agent_runs_page_exposes_langgraph_event_timeline():
    client = TestClient(app)
    response = client.get("/ui/agent-runs")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "run-events" in response.text
    assert "事件流" in response.text
    assert "loadRunEvents" in main_js
    assert "subscribeAgentRunEvents" in main_js
    assert "event-timeline" in style_css
    assert "event-row" in style_css


def test_profiles_page_exposes_complete_chinese_resume_sections():
    client = TestClient(app)
    response = client.get("/ui/profiles")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    for text in [
        "选择简历栏目",
        "基础信息",
        "求职意向",
        "教育经历",
        "实习/工作经历",
        "项目经历",
        "校园/实践经历",
        "技能",
        "证书、荣誉与语言",
        "简历照片",
        "个人总结",
        "作品链接",
        "到岗时间",
    ]:
        assert text in response.text
    for section in ["photo", "summary", "work", "campus", "extras"]:
        assert f'data-resume-section="{section}" hidden' in response.text
    for section in ["intent", "education", "projects", "skills"]:
        assert f'data-profile-section-toggle value="{section}" checked' in response.text
    for field in [
        "photo_file",
        "education_school",
        "work_company",
        "project_tech_stack",
        "campus_organization",
        "certifications",
        "portfolio_links",
    ]:
        assert f'name="{field}"' in response.text
    for repeat_name in ["education", "work", "projects", "campus"]:
        assert f'data-repeat-list="{repeat_name}"' in response.text
        assert f'data-repeat-add="{repeat_name}"' in response.text
    assert "data-repeat-remove" in response.text
    assert "campus_experience" in main_js
    assert "certifications: resumeSectionEnabled" in main_js
    assert "selectedProfileSections" in main_js
    assert "updateProfileSectionVisibility" in main_js
    assert "readProfilePhotoDataUrl" in main_js
    assert "collectRepeatList" in main_js
    assert "addRepeatEntry" in main_js
    assert "removeRepeatEntry" in main_js
    assert "resume-section-map" in style_css
    assert "resume-form-section" in style_css
    assert "section-picker" in style_css
    assert "photo-preview-box" in style_css
    assert "repeat-entry" in style_css


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
