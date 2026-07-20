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
    assert "告诉 CareerAgent 你想找什么" in response.text
    assert "求职需求" in response.text
    assert "求职需求和简历任选一项即可" in response.text
    assert "常见搜索" in response.text
    assert "Agent 开发实习" in response.text
    assert "LLM 应用后端" in response.text
    assert "RAG 工程实习" in response.text
    assert 'data-demo-scenario="match"' in response.text
    assert 'data-demo-scenario="backend"' in response.text
    assert 'data-demo-scenario="rag"' in response.text
    assert "请选择本次是否使用简历" in response.text
    assert "本次只搜索岗位" in response.text
    assert "不使用简历" in response.text
    assert "上传并自动建立档案" in response.text
    assert "手动填写或自然语言生成" in response.text
    assert "profile-picker-dialog" in response.text
    assert "profile-picker-search" in response.text
    assert "open-profile-picker" in response.text
    assert "resume-source-card" in response.text
    assert "resume-picker-heading" in response.text
    assert "resume-selection-summary" in response.text
    assert "use-no-resume" in response.text
    assert "resume-upload-card" in response.text
    assert "selected-profile-card" in response.text
    assert "side-stack" in response.text
    assert "process-panel" in response.text
    assert "三选一提供简历档案" not in response.text
    assert 'name="selected_actions"' not in response.text
    assert 'name="name"' not in response.text
    assert 'name="email"' not in response.text
    assert 'name="skills"' not in response.text
    assert 'name="project"' not in response.text
    assert "career-start-form" in response.text
    assert "career-flow-steps" in response.text
    assert "career-flow-result" in response.text
    assert "岗位发现进度" in response.text
    assert 'data-stage="results"' in response.text
    assert 'name="source_mode"' in response.text
    assert 'name="internship_only"' in response.text
    assert "active-run-monitor" in response.text
    assert "active-run-list" in main_js
    assert "逐条确认，互不覆盖" in main_js
    assert "const orderedRows" in main_js
    assert "ACTIVE_RUN_COLLAPSED_KEY" in main_js
    assert "data-toggle-active-runs" in main_js
    assert ".active-run-monitor.collapsed" in style_css
    assert "llm-global-warning" in response.text
    assert "name=\"job_id\"" not in response.text
    assert "name=\"jd_text\"" not in response.text
    assert "搜索岗位" in response.text
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
    assert "clearStartResumeSelection" in main_js
    assert "data-select-profile" in main_js
    assert "JOB_DISCOVERY_SESSION_KEY" in main_js
    assert "/job-discovery/sessions" in main_js
    assert "preference_text:" in main_js
    assert "profile_id: profileId" in main_js
    assert "window.location.assign(`/ui/jobs?session_id=${body.session.id}`)" in main_js
    assert "当前未使用简历" in main_js
    assert "搜索并匹配岗位" in main_js
    assert "解析简历并匹配岗位" in main_js
    assert "businessSummaryHtml" in main_js
    assert "/agent/runs/${runId}/summary" in main_js
    assert "approval_bypass_detected" in main_js
    assert "ACTIVE_RUN_KEY" in main_js
    assert "careeragent.active_runs" in main_js
    assert "trackActiveRun" in main_js
    assert "restoreActiveRuns" in main_js
    assert "renderActiveRunMonitor" in main_js
    assert "if (dashboardRun) await restoreCareerFlowFromRun" not in main_js
    assert "ACTIVE_RUN_RECENT_TTL_MS" in main_js
    assert "DISMISSED_RUN_KEY" in main_js
    assert "updateTrackedRun" in main_js
    assert "dismissActiveRun" in main_js
    assert "recentRunsFromServer" in main_js
    assert "await api(\"/agent/runs\")" in main_js
    assert "data-dismiss-active-run" in main_js
    assert "最近完成的求职流程" in main_js
    assert "LLM 尚未接入" in main_js
    assert "loadGlobalLLMWarning" in main_js
    assert "LLM_DEPENDENT_PAGES" in main_js
    assert '"jobs"' in main_js
    assert '"applications"' in main_js
    assert "llm-global-warning" in style_css
    assert ".llm-global-warning[hidden]" in style_css
    assert "/agent/runs/${runId}" in main_js
    assert "/agent/runs/${run.id}/steps" in main_js
    assert "刷新或切换页面不会丢失进度" in main_js
    assert "查看历史记录" in main_js
    assert "EventSource" in main_js
    assert "/agent/runs/background" in main_js
    assert "/events/stream" in main_js
    assert "golden-demo-picker" in style_css
    assert "flow-stepper" in style_css
    assert "guidance-card" in style_css
    assert "resume-picker-heading" in style_css
    assert "resume-selection-summary" in style_css
    assert "start-resume-picker" in style_css
    assert "resume-source-card.is-selected" in style_css
    assert "profile-picker-dialog" in style_css
    assert ".process-panel .process-grid" in style_css
    assert ".start-submit-row" in style_css
    assert ".discovery-stepper" in style_css
    assert "accent-color: var(--green)" in style_css
    assert "white-space: nowrap" in style_css
    assert "min-height: 38px" in style_css
    assert "console-entry" in style_css


def test_run_history_page_exposes_business_summary_before_trace():
    client = TestClient(app)
    response = client.get("/ui/agent-runs")

    assert response.status_code == 200
    assert "请选择一条历史记录" in response.text
    assert "run-business-summary" in response.text
    assert response.text.index("run-business-summary") < response.text.index("run-steps")
    assert response.text.index("run-steps") < response.text.index("run-events")


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
    assert "job-search-workspace" in response.text
    assert "job-discovery-form" in response.text
    assert "求职需求" in response.text
    assert "本次简历" in response.text
    assert "不使用简历" in response.text
    assert "open-job-search-profile-picker" in response.text
    assert "clear-job-search-profile" in response.text
    assert "job-search-profile-picker-dialog" in response.text
    assert "job-results-panel" in response.text
    assert "manual-job-panel" in response.text
    assert ".job-discovery-form" in style_css
    assert ".job-search-filter-row" in style_css
    assert ".job-result-card" in style_css
    assert "runJobDiscovery" in main_js
    assert "renderJobDiscovery" in main_js
    assert "loadJobSearchSelectedProfile" in main_js
    assert "control?.classList.add(\"is-no-resume\")" in main_js
    assert "jobDetailUrl" in main_js
    assert "/ui/jobs/${jobId}" in main_js
    assert ".notice:empty" in style_css


def test_job_detail_page_exposes_optional_profile_match_and_tailor_flow():
    client = TestClient(app)
    response = client.get("/ui/jobs/123?session_id=7")
    main_js = Path("app/static/js/main.js").read_text(encoding="utf-8")
    style_css = Path("app/static/css/style.css").read_text(encoding="utf-8")

    assert response.status_code == 200
    assert 'data-job-id="123"' in response.text
    assert "岗位 JD" in response.text
    assert "匹配与差距" in response.text
    assert "定制简历" in response.text
    assert "尚未选择简历" in response.text
    assert "选择已有简历" in response.text
    assert "上传 PDF" in response.text
    assert "去建立档案" in response.text
    assert "run-job-match" in response.text
    assert "run-job-tailor" in response.text
    assert "评分、修改建议和事实检查会单独展示" in response.text
    assert "loadJobDetail" in main_js
    assert "runJobDetailMatch" in main_js
    assert "runJobDetailTailor" in main_js
    assert "const button = event.currentTarget" in main_js
    assert "button.disabled = false" in main_js
    assert 'api("/matches"' in main_js
    assert 'api("/resumes/tailor"' in main_js
    assert 'required_skill_coverage: "必备技能覆盖"' in main_js
    assert 'semantic_similarity: "语义相关度"' in main_js
    assert "applyLink.hidden = !job.apply_url" in main_js
    assert ".job-detail-grid" in style_css
    assert ".job-detail-tabs" in style_css
    assert ".job-gap-grid" in style_css
    assert "[hidden]" in style_css
    assert "display: none !important" in style_css


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
    assert "查看运行记录、阶段进度和失败原因" in response.text
    assert "最近 50 条记录" in response.text
    assert "run-history-count" in response.text
    assert "selected-run-title" in response.text
    assert "run-history-grid" in response.text
    assert "run-history-grid" in style_css
    assert "run-history-list" in style_css
    assert "run-history-select" in style_css
    assert "我的求职流程" not in response.text
    assert "agent-run-form" not in response.text
    assert "run-confirmation" in response.text
    assert "run-events" in response.text
    assert "事件流" in response.text
    assert "renderRunConfirmation" in main_js
    assert "const initialRun" in main_js
    assert "RUN_HISTORY_LIMIT = 50" in main_js
    assert "data-history-run-id" in main_js
    assert 'historyMode: "push"' in main_js
    assert "updateRunHistoryUrl" in main_js
    assert "confirmationContext" in main_js
    assert "选择一个岗位继续" in main_js
    assert "确认生成投递材料" in main_js
    assert "data-select-job-run" in main_js
    assert "data-confirm-application-run" in main_js
    assert "run-confirmation-dialog" in response.text
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
