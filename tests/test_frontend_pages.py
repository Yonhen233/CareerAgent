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


def test_profiles_page_exposes_resume_review_controls():
    client = TestClient(app)
    response = client.get("/ui/profiles")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "natural-profile-form" in response.text
    assert "自然语言建档" in response.text
    assert "生成简历档案" in response.text
    assert "upload-profile-form" in response.text
    assert "guided-profile-form" in response.text
    assert "resume-review-job-id" in response.text
    assert "针对岗位 ID（可选）" in response.text
    assert "renderNaturalProfileResult" in main_js
    assert 'selected_actions: ["create_profile"]' in main_js
    assert "natural-profile-result" in main_js
    assert "data-review-profile" in main_js
    assert "reviewProfile" in main_js
    assert "/profiles/${profileId}/review" in main_js
    assert "renderResumeReview" in main_js
    assert "RAG 已接入" in main_js
    assert "resume-review-card" in style_css
    assert "score-grid" in style_css


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
    assert "三选一提供简历档案，再开始找岗位" in response.text
    assert "请先在三种方式中任选一种提供简历档案" in response.text
    assert response.text.index("你想让 Agent 做什么") < response.text.index("流程内容") < response.text.index("三选一提供简历档案")
    assert "方式一" in response.text
    assert "方式二" in response.text
    assert "方式三" in response.text
    assert "流程内容" in response.text
    assert "固定步骤" in response.text
    assert "选择 PDF 后会自动解析，并把解析结果作为本次流程的简历档案" in response.text
    assert "profile-picker-dialog" in response.text
    assert "profile-picker-search" in response.text
    assert "open-profile-picker" in response.text
    assert "resume-source-card" in response.text
    assert "resume-source-guidance" in response.text
    assert "resume-upload-card" in response.text
    assert "selected-profile-card" in response.text
    assert "side-stack" in response.text
    assert "process-panel" in response.text
    assert "信息会自动合并" not in response.text
    assert 'value="create_profile"' not in response.text
    assert 'value="search_jobs"' not in response.text
    for action in ["tailor_resume", "quick_apply", "interview_prep"]:
        assert f'value="{action}"' in response.text
    assert 'name="name"' not in response.text
    assert 'name="email"' not in response.text
    assert 'name="skills"' not in response.text
    assert 'name="project"' not in response.text
    assert "需求入口" not in response.text
    assert "一键开始" not in response.text
    assert "career-start-form" in response.text
    assert "career-flow-steps" in response.text
    assert "career-flow-result" in response.text
    assert "active-run-monitor" in response.text
    assert "name=\"job_id\"" in response.text
    assert "name=\"jd_text\"" in response.text
    assert "开始处理" in response.text
    assert "控制台" in response.text
    assert ">历史记录<" in response.text
    assert ">流程<" not in response.text
    assert ">运维<" not in response.text
    assert "dashboard-ops-summary" not in response.text
    assert "runCareerStartFlow" in main_js
    assert "runNaturalLanguageRequest" in main_js
    assert "updateCareerFlowFromNaturalResult" in main_js
    assert "packageLabel" in main_js
    assert "parseResumeFileIntoStartForm" in main_js
    assert "populateStartFormFromProfile" in main_js
    assert "openProfilePicker" in main_js
    assert "renderProfilePickerList" in main_js
    assert "selectProfileFromPicker" in main_js
    assert "profileSummaryText" in main_js
    assert "updateResumeSourceSelection" in main_js
    assert "data-select-profile" in main_js
    assert "profileContextFromStartForm" in main_js
    assert 'const required = ["create_profile", "search_jobs"]' in main_js
    assert "固定完成简历档案、岗位搜索和匹配排序" in main_js
    assert "optionalStartActions(form).length" in main_js
    assert "selected_actions: actions" in main_js
    assert "profile_context: null" in main_js
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
    assert "ACTIVE_RUN_KEY" in main_js
    assert "careeragent.active_runs" in main_js
    assert "trackActiveRun" in main_js
    assert "restoreActiveRuns" in main_js
    assert "renderActiveRunMonitor" in main_js
    assert "restoreCareerFlowFromRun" in main_js
    assert "/agent/runs/${runId}" in main_js
    assert "/agent/runs/${run.id}/steps" in main_js
    assert "刷新或切换页面不会丢失进度" in main_js
    assert "查看历史记录" in main_js
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
    assert "resume-source-guidance" in style_css
    assert "fixed-flow-steps" in style_css
    assert "start-resume-picker" in style_css
    assert "resume-source-card.is-selected" in style_css
    assert "profile-picker-dialog" in style_css
    assert ".process-panel .process-grid" in style_css
    assert "grid-template-columns: repeat(3" in style_css
    assert "accent-color: var(--green)" in style_css
    assert "white-space: nowrap" in style_css
    assert "min-height: 38px" in style_css
    assert "console-entry" in style_css


def test_profiles_entry_panels_are_user_facing_and_aligned():
    client = TestClient(app)
    response = client.get("/ui/profiles")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "上传 PDF 简历" in response.text
    assert "适合已经有成稿简历" in response.text
    assert "描述经历，让 Agent 生成简历档案" in response.text
    assert "适合还没有完整简历" in response.text
    assert "profile-entry-panel" in response.text
    assert "span-5 profile-entry-panel" in response.text
    assert "span-7 profile-guide-panel profile-entry-panel" in response.text
    assert "profile-entry-grid" in style_css
    assert "profile-entry-panel" in style_css
    assert "min-height: 320px" in style_css
    assert "profile-entry-form button" in style_css


def test_jobs_page_entry_panels_are_aligned():
    client = TestClient(app)
    response = client.get("/ui/jobs")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "job-entry-grid" in response.text
    assert "span-5 job-entry-panel" in response.text
    assert "span-7 job-entry-panel" in response.text
    assert "job-entry-form" in response.text
    assert "job-entry-grid" in style_css
    assert "job-entry-panel" in style_css
    assert "job-entry-form button" in style_css
    assert "/jobs/${row.id}/html" in main_js
    assert "预览 JD" in main_js
    assert ".notice:empty" in style_css
    assert "margin-top: auto" in style_css


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
    client = TestClient(app)
    response = client.get("/ui/resumes")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "tailor-picker-grid" in response.text
    assert "tailor-profile-picker-dialog" in response.text
    assert "tailor-job-picker-dialog" in response.text
    assert "tailor-review-result" in response.text
    assert "评分、问题和修改建议只显示在页面上，不写入简历正文" in response.text
    assert "/profiles/${row.id}/html" in main_js
    assert "预览简历" in main_js
    assert "/resumes/${row.id}/html" in main_js
    assert "resume-preview-frame" in main_js
    assert "打开 HTML 预览" in main_js
    assert "下载 Markdown" in main_js
    assert "openTailorProfilePicker" in main_js
    assert "openTailorJobPicker" in main_js
    assert "renderTailoredResumeDiagnostics" in main_js
    assert "tailor-review-result" in main_js
    assert "/profiles/${profileId}/review" in main_js
    assert "resume-preview-frame" in style_css
    assert "tailor-picker-grid" in style_css
    assert "tailor-select-card" in style_css
    assert "tailor-diagnostics" in style_css


def test_agent_runs_page_exposes_langgraph_event_timeline():
    client = TestClient(app)
    response = client.get("/ui/agent-runs")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "求职历史记录" in response.text
    assert "查看一键流程和分步页面产生的运行记录" in response.text
    assert "最近运行" in response.text
    assert "run-history-grid" in response.text
    assert "run-history-grid" in style_css
    assert "我的求职流程" not in response.text
    assert "agent-run-form" not in response.text
    assert "run-confirmation" in response.text
    assert "run-events" in response.text
    assert "事件流" in response.text
    assert "renderRunConfirmation" in main_js
    assert "await loadRunSteps(rows[0].id)" in main_js
    assert "高风险动作的确认点" in main_js
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
