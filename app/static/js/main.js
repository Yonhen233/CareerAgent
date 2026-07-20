const $ = (selector) => document.querySelector(selector);
const ADMIN_TOKEN_KEY = "careeragent.admin_token";
const ACTIVE_RUN_KEY = "careeragent.active_runs";
const DISMISSED_RUN_KEY = "careeragent.dismissed_runs";
const ACTIVE_RUN_COLLAPSED_KEY = "careeragent.active_runs_collapsed";
const JOB_DISCOVERY_SESSION_KEY = "careeragent.job_discovery_session";
const ACTIVE_RUN_TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled", "canceled"]);
const ACTIVE_RUN_RECENT_TTL_MS = 24 * 60 * 60 * 1000;
const LLM_DEPENDENT_PAGES = new Set([
  "dashboard",
  "profiles",
  "jobs",
  "resumes",
  "applications",
  "interview_prep",
  "evaluations",
]);
let activeRunEventSource = null;
let activeRunMonitorTimer = null;
let profilePickerRows = [];
let jobPickerRows = [];
let jobSearchProfileRows = [];
let jobDetailProfileRows = [];
let currentJobDiscovery = null;
let currentJobDetail = null;

function toast(message) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
  setTimeout(() => {
    el.hidden = true;
  }, 3600);
}

function getAdminToken() {
  return window.localStorage?.getItem(ADMIN_TOKEN_KEY) || "";
}

function authHeaders(headers = {}) {
  const token = getAdminToken();
  return {
    ...headers,
    ...(token ? { "X-Admin-Token": token } : {}),
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: authHeaders({ "Content-Type": "application/json", ...(options.headers || {}) }),
    ...options,
  });
  if (!response.ok) {
    let detail = await response.text();
    let parsed = null;
    try {
      parsed = JSON.parse(detail);
    } catch (_) {
      // keep text
    }
    if (parsed?.user_message) {
      const error = new Error(parsed.user_message);
      error.body = parsed;
      throw error;
    }
    detail = parsed?.detail || parsed?.error || detail;
    throw new Error(detail);
  }
  return response.json();
}

function renderItems(target, items, renderer) {
  const el = $(target);
  if (!el) return;
  if (!items.length) {
    el.innerHTML = `<div class="item meta">暂无数据</div>`;
    return;
  }
  el.innerHTML = items.map(renderer).join("");
  if (window.lucide) window.lucide.createIcons();
}

function tags(values) {
  return `<div class="tags">${(values || []).slice(0, 8).map((x) => `<span class="tag">${escapeHtml(x)}</span>`).join("")}</div>`;
}

function taskLabel(taskType) {
  const labels = {
    full_career_flow: "完整求职流程",
    find_jobs_for_profile: "岗位推荐",
    tailor_resume_for_job: "定制简历",
    quick_apply: "投递材料",
    prepare_interview_for_job: "面试准备",
    natural_language_request: "智能需求处理",
  };
  return labels[taskType] || taskType || "求职流程";
}

function stepLabel(stepName) {
  const labels = {
    plan_task: "理解任务",
    load_profile: "读取简历",
    search_jobs: "搜索岗位",
    match_job: "匹配岗位",
    tailor_resume_with_rag: "生成定制简历",
    create_missing_tailored_resume: "生成定制简历",
    fit_gate: "投递前适配检查",
    create_application_packet: "生成投递材料",
    generate_interview_prep: "生成面试准备包",
    load_job: "读取岗位",
    parse_user_request: "理解自然语言需求",
    execute_user_plan: "执行需求",
    repair_user_plan: "自动修复计划",
  };
  return labels[stepName] || stepName || "处理阶段";
}

function interviewSourceLabel(source) {
  const labels = {
    source_backed_interview_experience: "已导入面经",
    online_experience_research: "同岗面经调研",
    resume_project_evidence: "项目证据",
    resume_project_stack: "项目技术栈",
    llm_project_implementation: "LLM 项目追问",
    llm_foundation_drill: "LLM 八股追问",
    jd_technical_depth: "JD 技术",
    jd_gap_drill: "缺口追问",
    general_interview: "通用问题",
  };
  return labels[source] || source || "未标注";
}

function interviewAngleLabel(angle) {
  const labels = {
    same_role_interview_experience: "网上同岗面经",
    resume_project_tech_stack: "项目技术栈",
    other_possible_interview_questions: "其他问题",
  };
  return labels[angle] || angle || "其他问题";
}

function validationList(items, emptyText) {
  if (!items || !items.length) return `<div class="meta">${escapeHtml(emptyText)}</div>`;
  return `<ul class="compact-list">${items.map((item) => `
    <li>${escapeHtml(item.message || item.code || "")}${item.terms?.length ? `：${escapeHtml(item.terms.join("、"))}` : ""}</li>
  `).join("")}</ul>`;
}

function applicationValidation(row) {
  const automation = row.automation_result_json || {};
  const validation = automation.packet_validation || {};
  const risk = validation.risk_level || "unknown";
  const passed = validation.passed === true;
  return `
    <div class="validation-panel ${passed ? "validation-ok" : "validation-risk"}">
      <div class="validation-head">
        <span class="status-pill ${passed ? "ok" : ""}">${passed ? "可投递" : "需检查"}</span>
        <span class="meta">最终提交前仍需要你人工确认</span>
      </div>
      <div class="validation-grid">
        <div>
          <strong>阻断问题</strong>
          ${validationList(validation.issues || [], "未发现阻断问题")}
        </div>
        <div>
          <strong>提醒</strong>
          ${validationList(validation.warnings || [], "无警告")}
        </div>
      </div>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formJson(form) {
  const data = new FormData(form);
  const obj = {};
  for (const [key, value] of data.entries()) {
    if (typeof File !== "undefined" && value instanceof File) continue;
    if (value === "") continue;
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      obj[key] = Array.isArray(obj[key]) ? [...obj[key], value] : [obj[key], value];
    } else {
      obj[key] = value;
    }
  }
  return obj;
}

function splitList(value) {
  return String(value || "")
    .split(/[,，;；\n]/)
    .map((x) => x.trim())
    .filter(Boolean);
}

function hasAnyValue(...values) {
  return values.some((value) => String(value || "").trim());
}

function compactObject(value) {
  return Object.fromEntries(
    Object.entries(value || {}).filter(([, item]) => {
      if (Array.isArray(item)) return item.length > 0;
      return item !== undefined && item !== null && String(item).trim() !== "";
    })
  );
}

function valueIn(container, name) {
  return container.querySelector(`[name="${name}"]`)?.value?.trim() || "";
}

function repeatEntries(form, listName) {
  return Array.from(form?.querySelectorAll(`[data-repeat-list="${listName}"] [data-repeat-item]`) || []);
}

function collectRepeatList(form, listName, mapper) {
  return repeatEntries(form, listName)
    .map((entry) => mapper(entry))
    .filter((item) => hasAnyValue(...Object.values(item).flat()));
}

function selectedProfileSections(form) {
  return Array.from(form.querySelectorAll("[data-profile-section-toggle]:checked")).map((input) => input.value);
}

function resumeSectionEnabled(enabled, section) {
  return !enabled || enabled.has(section);
}

function updateProfileSectionVisibility(form) {
  if (!form) return;
  const enabled = new Set(selectedProfileSections(form));
  form.querySelectorAll("[data-resume-section]").forEach((section) => {
    const isEnabled = enabled.has(section.dataset.resumeSection);
    section.hidden = !isEnabled;
    section.querySelectorAll("input, textarea, select, button[data-repeat-add], button[data-repeat-remove]").forEach((control) => {
      control.disabled = !isEnabled;
    });
  });
}

function updateRepeatListLabels(list) {
  const label = list.dataset.repeatLabel || "经历";
  const entries = Array.from(list.querySelectorAll("[data-repeat-item]"));
  entries.forEach((entry, index) => {
    const title = entry.querySelector(".repeat-entry-head strong");
    if (title) title.textContent = `${label} ${index + 1}`;
    const removeButton = entry.querySelector("[data-repeat-remove]");
    if (removeButton) removeButton.hidden = entries.length <= 1;
  });
}

function clearRepeatEntry(entry) {
  entry.querySelectorAll("input, textarea, select").forEach((control) => {
    if (control.type === "checkbox" || control.type === "radio") {
      control.checked = false;
    } else {
      control.value = "";
    }
  });
}

function addRepeatEntry(form, listName) {
  const list = form.querySelector(`[data-repeat-list="${listName}"]`);
  const template = list?.querySelector("[data-repeat-item]");
  if (!list || !template) return;
  const clone = template.cloneNode(true);
  clearRepeatEntry(clone);
  list.appendChild(clone);
  updateRepeatListLabels(list);
  clone.querySelector("input, textarea, select")?.focus();
  if (window.lucide) window.lucide.createIcons();
}

function removeRepeatEntry(button) {
  const entry = button.closest("[data-repeat-item]");
  const list = button.closest("[data-repeat-list]");
  if (!entry || !list) return;
  const entries = list.querySelectorAll("[data-repeat-item]");
  if (entries.length <= 1) {
    clearRepeatEntry(entry);
  } else {
    entry.remove();
  }
  updateRepeatListLabels(list);
}

function initializeRepeatLists(form) {
  form?.querySelectorAll("[data-repeat-list]").forEach(updateRepeatListLabels);
}

function resetRepeatLists(form) {
  form?.querySelectorAll("[data-repeat-list]").forEach((list) => {
    const entries = Array.from(list.querySelectorAll("[data-repeat-item]"));
    entries.slice(1).forEach((entry) => entry.remove());
    updateRepeatListLabels(list);
  });
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("照片读取失败"));
    reader.readAsDataURL(file);
  });
}

async function readProfilePhotoDataUrl(form) {
  const file = form.elements.photo_file?.files?.[0];
  if (!file || file.size === 0) return null;
  if (!file.type.startsWith("image/")) {
    throw new Error("请上传 PNG、JPG 或 WebP 格式的照片。");
  }
  if (file.size > 1.5 * 1024 * 1024) {
    throw new Error("照片文件不能超过 1.5MB。");
  }
  return await readFileAsDataUrl(file);
}

async function updateProfilePhotoPreview(input) {
  const preview = $("#profile-photo-preview");
  if (!preview) return;
  const file = input.files?.[0];
  if (!file || file.size === 0) {
    preview.hidden = true;
    preview.removeAttribute("src");
    return;
  }
  const dataUrl = await readProfilePhotoDataUrl(input.form);
  preview.src = dataUrl;
  preview.hidden = false;
}

async function loadHealth() {
  const pill = $("#health-pill");
  if (!pill) return;
  const body = await api("/health");
  pill.textContent = body.llm_configured ? "LLM Ready" : "Offline Mode";
  pill.classList.toggle("ok", Boolean(body.llm_configured));
  pill.classList.toggle("risk", !body.llm_configured);
}

function pageNeedsLLMWarning() {
  return LLM_DEPENDENT_PAGES.has(document.body.dataset.page || "");
}

async function loadGlobalLLMWarning() {
  const el = $("#llm-global-warning");
  if (!el || !pageNeedsLLMWarning()) return;
  try {
    const body = await api("/health");
    if (body.llm_configured) {
      el.hidden = true;
      el.innerHTML = "";
      return;
    }
    el.hidden = false;
    el.innerHTML = `
      <strong>LLM 尚未接入</strong>
      <span>自然语言建档、简历评分、定制简历、面试准备和 LLM 评测需要配置 API Key 后才能正常运行。当前页面仍可浏览已有数据，提交相关任务会失败并返回可追踪错误。</span>
    `;
  } catch (error) {
    el.hidden = false;
    el.innerHTML = `
      <strong>无法确认 LLM 状态</strong>
      <span>${escapeHtml(error.message)}。依赖 LLM 的操作可能无法执行。</span>
    `;
  }
}

async function loadProfiles() {
  const rows = await api("/profiles");
  renderItems("#profiles-list", rows, (row) => `
    <article class="item">
      <div class="item-title"><span>#${row.id} ${escapeHtml(row.name || "未命名简历")}</span><span class="meta">${row.source_type === "pdf" ? "PDF 上传" : "手动填写"}</span></div>
      <div class="meta">${escapeHtml(row.headline || "")}</div>
      ${tags(row.structured_profile_json.skills || [])}
      <div class="flow-result-actions">
        <a class="button ghost" href="/profiles/${row.id}/html" target="_blank"><i data-lucide="eye"></i> 预览简历</a>
        <button class="button ghost" type="button" data-review-profile="${row.id}"><i data-lucide="gauge"></i> 简历评分</button>
      </div>
      <div id="profile-review-${row.id}" class="resume-review-slot"></div>
    </article>
  `);
}

function profileSummaryText(profile) {
  const structured = profile?.structured_profile_json || {};
  const roles = profile?.target_roles_json || structured.target_roles || [];
  const roleText = roles.length ? roles.slice(0, 2).join("、") : profile?.headline || structured.headline || "未填写求职意向";
  const skillCount = (structured.skills || []).length;
  return `${roleText}${skillCount ? ` · ${skillCount} 个技能` : ""}`;
}

function updateResumeSourceSelection(source) {
  $("#selected-profile-card")?.classList.toggle("is-selected", source === "existing");
  $("#resume-upload-card")?.classList.toggle("is-selected", source === "pdf");
}

function updateSelectedProfileCard(profile, source = "existing") {
  const title = $("#selected-profile-title");
  const summary = $("#selected-profile-summary");
  const form = $("#career-start-form");
  if (!title || !summary || !form) return;
  if (!profile) {
    title.textContent = "本次不使用简历";
    summary.textContent = "可以直接搜索，也可以选择档案获得匹配分析。";
    if (form.profile_id) form.profile_id.value = "";
    updateResumeSourceSelection(null);
    updateStartInputGuidance(form);
    return;
  }
  const structured = profile.structured_profile_json || {};
  title.textContent = `#${profile.id} ${profile.name || structured.name || "未命名简历"}`;
  summary.textContent = profileSummaryText(profile);
  if (form.profile_id) form.profile_id.value = profile.id || "";
  if (form.query && !form.query.value.trim()) {
    const roles = profile.target_roles_json || structured.target_roles || [];
    form.query.value = roles[0] || "Agent 开发实习生";
  }
  if (form.location && structured.location && !form.location.value.trim()) {
    form.location.value = structured.location;
  }
  updateResumeSourceSelection(source);
  updateStartInputGuidance(form);
}

async function openProfilePicker() {
  const dialog = $("#profile-picker-dialog");
  if (!dialog) return;
  if (!profilePickerRows.length) {
    profilePickerRows = await api("/profiles");
  }
  renderProfilePickerList();
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "open");
  $("#profile-picker-search")?.focus();
}

function closeProfilePicker() {
  const dialog = $("#profile-picker-dialog");
  if (!dialog) return;
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}

function renderProfilePickerList() {
  const el = $("#profile-picker-list");
  if (!el) return;
  const keyword = String($("#profile-picker-search")?.value || "").trim().toLowerCase();
  const rows = profilePickerRows.filter((profile) => {
    const structured = profile.structured_profile_json || {};
    const haystack = [
      profile.id,
      profile.name,
      structured.name,
      profile.email,
      structured.email,
      profile.headline,
      structured.headline,
      ...(profile.target_roles_json || structured.target_roles || []),
    ].join(" ").toLowerCase();
    return !keyword || haystack.includes(keyword);
  });
  if (!rows.length) {
    el.innerHTML = `<article class="item meta">没有匹配的简历档案</article>`;
    return;
  }
  el.innerHTML = rows.slice(0, 60).map((profile) => {
    const structured = profile.structured_profile_json || {};
    const skills = (structured.skills || []).slice(0, 6);
    return `
      <article class="profile-picker-item">
        <div>
          <div class="item-title">
            <span>#${profile.id} ${escapeHtml(profile.name || structured.name || "未命名简历")}</span>
            <span class="meta">${profile.source_type === "pdf" ? "PDF 上传" : "手动/自然语言"}</span>
          </div>
          <div class="meta">${escapeHtml(profileSummaryText(profile))}</div>
          ${skills.length ? tags(skills) : ""}
        </div>
        <div class="profile-picker-actions">
          <button class="button primary" type="button" data-select-profile="${profile.id}"><i data-lucide="check"></i> 选择</button>
          <a class="button ghost" href="/profiles/${profile.id}/html" target="_blank"><i data-lucide="eye"></i> 详情</a>
        </div>
      </article>
    `;
  }).join("");
  if (window.lucide) window.lucide.createIcons();
}

function selectProfileFromPicker(profileId) {
  const profile = profilePickerRows.find((item) => Number(item.id) === Number(profileId));
  if (!profile) return;
  updateSelectedProfileCard(profile);
  closeProfilePicker();
  toast(`已选择简历档案 #${profile.id}`);
}

async function reviewProfile(profileId) {
  const slot = $(`#profile-review-${profileId}`);
  const jobId = Number($("#resume-review-job-id")?.value || 0);
  if (slot) {
    slot.innerHTML = `<div class="item meta">正在评分，${jobId ? "会结合目标岗位和 RAG 证据" : "将先做通用简历体检"}...</div>`;
  }
  const review = await api(`/profiles/${profileId}/review`, {
    method: "POST",
    body: JSON.stringify({
      job_id: jobId > 0 ? jobId : null,
      include_llm: true,
    }),
  });
  if (slot) {
    slot.innerHTML = renderResumeReview(review);
    if (window.lucide) window.lucide.createIcons();
  }
  toast(`${jobId ? "岗位针对性" : "通用"}简历评分完成：${review.overall_score} 分`);
}

function renderResumeReview(review) {
  const dimensions = review.dimension_scores || {};
  const issues = review.issues || [];
  const suggestions = review.suggestions || [];
  const evidence = review.rag_evidence || [];
  const alignment = review.target_alignment || {};
  return `
    <section class="resume-review-card">
      <div class="resume-review-head">
        <div>
          <p class="eyebrow">${review.review_type === "targeted" ? "岗位针对性评分" : "通用简历评分"}</p>
          <strong>${escapeHtml(review.overall_score)} 分 · ${escapeHtml(review.grade || "-")}</strong>
        </div>
        <span class="status-pill ${review.overall_score >= 75 ? "ok" : review.overall_score < 60 ? "risk" : ""}">
          ${review.trace?.rag_used ? "RAG 已接入" : "通用体检"}
        </span>
      </div>
      ${alignment.match_score !== undefined ? `
        <div class="meta">目标岗位：${escapeHtml(alignment.company || "")} ${escapeHtml(alignment.job_title || "")} · 匹配 ${escapeHtml(alignment.match_score)}</div>
      ` : ""}
      <div class="score-grid">
        ${Object.entries(dimensions).map(([key, value]) => `
          <div class="score-cell">
            <span>${escapeHtml(resumeReviewDimensionLabel(key))}</span>
            <strong>${escapeHtml(value)}</strong>
          </div>
        `).join("")}
      </div>
      ${review.strengths?.length ? `
        <div class="review-block">
          <h3>优势</h3>
          <ul>${review.strengths.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </div>
      ` : ""}
      ${issues.length ? `
        <div class="review-block">
          <h3>主要问题</h3>
          <ul>${issues.map((item) => `<li><strong>${escapeHtml(item.section || "简历")}</strong>：${escapeHtml(item.problem || "")}</li>`).join("")}</ul>
        </div>
      ` : ""}
      ${suggestions.length ? `
        <div class="review-block">
          <h3>修改建议</h3>
          <ol>${suggestions.map((item) => `
            <li>
              <strong>${escapeHtml(resumeReviewPriorityLabel(item.priority))} · ${escapeHtml(item.section || "简历")}</strong>
              <p>${escapeHtml(item.advice || "")}</p>
              ${item.example_rewrite ? `<blockquote>${escapeHtml(item.example_rewrite)}</blockquote>` : ""}
            </li>
          `).join("")}</ol>
        </div>
      ` : ""}
      ${evidence.length ? `
        <details class="review-evidence">
          <summary>查看 RAG 证据（${evidence.length} 条）</summary>
          ${evidence.map((item) => `
            <article>
              <strong>${escapeHtml(item.chunk_type || "chunk")} · ${escapeHtml(item.score ?? "-")}</strong>
              <p>${escapeHtml(item.text || "")}</p>
            </article>
          `).join("")}
        </details>
      ` : ""}
    </section>
  `;
}

function renderNaturalProfileResult(body) {
  const result = $("#natural-profile-result");
  if (!result) return;
  const data = body.result_json || {};
  const profileId = data.profile?.id;
  const failed = body.status === "failed" || !profileId;
  result.innerHTML = `
    <article class="item ${failed ? "validation-risk" : "validation-ok"}">
      <div class="item-title">
        <span>${failed ? "生成失败" : `简历档案 #${escapeHtml(profileId)}`}</span>
        <span class="status-pill ${failed ? "risk" : "ok"}">${failed ? "需处理" : "已建立"}</span>
      </div>
      <div class="meta">${escapeHtml(body.user_message || naturalResultSummary(body))}</div>
      ${profileId ? `<div class="flow-result-actions">
        <a class="button ghost" href="/profiles/${profileId}/html" target="_blank"><i data-lucide="eye"></i> 预览简历</a>
        <button class="button ghost" type="button" data-review-profile="${profileId}"><i data-lucide="gauge"></i> 简历评分</button>
      </div>` : ""}
    </article>
  `;
  if (window.lucide) window.lucide.createIcons();
}

function resumeReviewDimensionLabel(key) {
  const labels = {
    profile_completeness: "完整度",
    evidence_strength: "证据强度",
    metric_density: "量化结果",
    keyword_clarity: "关键词",
    readability: "可读性",
    risk_control: "事实边界",
    target_alignment: "岗位匹配",
    required_skill_coverage: "必备技能覆盖",
    preferred_skill_coverage: "加分技能覆盖",
    semantic_similarity: "语义相关度",
    evidence_relevance: "经历证据",
    internship_fit: "实习条件",
    negative_evidence_penalty: "风险扣分",
  };
  return labels[key] || key;
}

function resumeReviewPriorityLabel(priority) {
  const labels = { high: "高优先级", medium: "中优先级", low: "低优先级" };
  return labels[priority] || "中优先级";
}

async function loadJobs() {
  if ($("#job-detail-page")) {
    await loadJobDetail();
    return;
  }
  if (!$("#jobs-list")) return;
  const params = new URLSearchParams(window.location.search);
  const sessionId = Number(params.get("session_id") || window.localStorage?.getItem(JOB_DISCOVERY_SESSION_KEY) || 0);
  if (sessionId > 0) {
    try {
      const body = await api(`/job-discovery/sessions/${sessionId}`);
      renderJobDiscovery(body);
      return;
    } catch (error) {
      window.localStorage?.removeItem(JOB_DISCOVERY_SESSION_KEY);
      toast(`上次搜索记录无法恢复：${error.message}`);
    }
  }
  const rows = await api("/jobs");
  renderJobDiscovery({
    session: null,
    results: rows.map((job, index) => ({
      id: `job-${job.id}`,
      rank: index + 1,
      retrieval_score: null,
      match_score: null,
      final_score: null,
      reason: {},
      job,
    })),
  });
}

function jobDetailUrl(jobId, sessionId = null, profileId = null) {
  const params = new URLSearchParams();
  if (sessionId) params.set("session_id", sessionId);
  if (profileId) params.set("profile_id", profileId);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return `/ui/jobs/${jobId}${suffix}`;
}

function renderJobDiscovery(body) {
  currentJobDiscovery = body;
  const session = body.session;
  const results = body.results || [];
  const title = $("#job-results-title");
  const summary = $("#job-session-summary");
  const errors = $("#job-search-errors");
  if (session) {
    window.localStorage?.setItem(JOB_DISCOVERY_SESSION_KEY, String(session.id));
    const params = new URLSearchParams(window.location.search);
    params.set("session_id", session.id);
    window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
    if (title) title.textContent = `找到 ${results.length} 个岗位`;
    const modeLabels = {
      preference_only: "按求职需求搜索",
      profile_only: "按简历自动匹配",
      preference_and_profile: "结合求职需求和简历匹配",
      browse: "浏览 Agent 岗位",
    };
    if (summary) {
      summary.textContent = `${modeLabels[session.input_mode] || "岗位搜索"} · 搜索记录 #${session.id} · ${session.resolved_query}`;
    }
    const sourceErrors = Object.entries(session.source_errors_json || {});
    if (errors) {
      errors.innerHTML = sourceErrors.length
        ? `<strong>部分岗位来源暂时不可用</strong><span>${sourceErrors.map(([name]) => escapeHtml(name)).join("、")}；其余来源和岗位库结果已正常展示。</span>`
        : "";
    }
    const form = $("#job-search-form");
    if (form) {
      if (form.preference_text) form.preference_text.value = session.preference_text || "";
      if (form.profile_id) form.profile_id.value = session.profile_id || "";
      if (form.location) form.location.value = session.location || "";
      if (form.source_mode) form.source_mode.value = session.source_mode || "hybrid";
      if (form.internship_only) form.internship_only.checked = Boolean(session.internship_only);
    }
    if (session.profile_id) {
      loadJobSearchSelectedProfile(session.profile_id).catch(() => {});
    } else {
      updateJobSearchProfile(null);
    }
  } else {
    if (title) title.textContent = `岗位库（${results.length}）`;
    if (summary) summary.textContent = "这是已经同步到系统的岗位。输入求职需求后，可以获得针对性的检索排序。";
    if (errors) errors.innerHTML = "";
  }

  renderItems("#jobs-list", results, (result) => {
    const row = result.job;
    const structured = row.structured_jd_json || {};
    const reasons = result.reason?.relevance_reasons || [];
    const matched = result.reason?.matched_skills || [];
    const missing = result.reason?.missing_skills || [];
    const scoreLabel = result.match_score !== null && result.match_score !== undefined
      ? `简历匹配 ${Math.round(result.match_score)}`
      : result.retrieval_score !== null && result.retrieval_score !== undefined
        ? `需求相关 ${Math.round(result.retrieval_score)}`
        : "岗位库";
    return `
      <article class="job-result-card">
        <div class="job-result-rank">${result.rank || ""}</div>
        <div class="job-result-body">
          <div class="item-title">
            <span>${escapeHtml(row.title)}</span>
            <span class="status-pill ${result.match_score >= 75 ? "ok" : ""}">${escapeHtml(scoreLabel)}</span>
          </div>
          <div class="job-result-meta">
            <span>${escapeHtml(row.company || "未知公司")}</span>
            <span>${escapeHtml(row.location || "地点未注明")}</span>
            <span>${escapeHtml(row.job_type || "类型未注明")}</span>
            <span>${escapeHtml(row.source || "manual")}</span>
          </div>
          ${tags(structured.required_skills || structured.keywords || [])}
          ${reasons.length ? `<p class="job-result-reason"><strong>为什么出现：</strong>${escapeHtml(reasons.slice(0, 3).join("；"))}</p>` : ""}
          ${matched.length || missing.length ? `
            <div class="job-match-preview">
              <span><strong>已匹配</strong> ${escapeHtml(matched.slice(0, 5).join("、") || "等待详细分析")}</span>
              <span><strong>需补充</strong> ${escapeHtml(missing.slice(0, 4).join("、") || "暂无明显缺口")}</span>
            </div>
          ` : ""}
          <div class="flow-result-actions">
            <a class="button primary" href="${jobDetailUrl(row.id, session?.id, session?.profile_id)}"><i data-lucide="panel-right-open"></i> 查看岗位详情</a>
            ${row.apply_url ? `<a class="button ghost" href="${escapeHtml(row.apply_url)}" target="_blank" rel="noopener"><i data-lucide="external-link"></i> 官方投递页</a>` : ""}
          </div>
        </div>
      </article>
    `;
  });
}

async function runJobDiscovery(form) {
  const raw = formJson(form);
  const submit = form.querySelector("button[type='submit']");
  if (submit) submit.disabled = true;
  const errors = $("#job-search-errors");
  if (errors) errors.innerHTML = `<span>正在检索真实岗位来源和岗位库，请稍候...</span>`;
  try {
    const body = await api("/job-discovery/sessions", {
      method: "POST",
      body: JSON.stringify({
        preference_text: String(raw.preference_text || "").trim() || null,
        profile_id: raw.profile_id ? Number(raw.profile_id) : null,
        location: raw.location || null,
        internship_only: Boolean(form.internship_only?.checked),
        limit: Number(raw.limit || 20),
        source_mode: raw.source_mode || "hybrid",
      }),
    });
    renderJobDiscovery(body);
    toast(`已找到 ${body.results.length} 个岗位`);
  } finally {
    if (submit) submit.disabled = false;
  }
}

function updateJobSearchProfile(profile) {
  const form = $("#job-search-form");
  const title = $("#job-search-profile-title");
  const summary = $("#job-search-profile-summary");
  if (!form || !title || !summary) return;
  if (!profile) {
    form.profile_id.value = "";
    title.textContent = "未选择简历";
    summary.textContent = "本次只按求职需求搜索。";
    return;
  }
  const structured = profile.structured_profile_json || {};
  form.profile_id.value = profile.id;
  title.textContent = `#${profile.id} ${profile.name || structured.name || "未命名简历"}`;
  summary.textContent = profileSummaryText(profile);
}

async function loadJobSearchSelectedProfile(profileId) {
  const profile = await api(`/profiles/${profileId}`);
  updateJobSearchProfile(profile);
}

async function openJobSearchProfilePicker() {
  if (!jobSearchProfileRows.length) jobSearchProfileRows = await api("/profiles");
  renderJobSearchProfilePicker();
  openDialog("#job-search-profile-picker-dialog");
  $("#job-search-profile-picker-search")?.focus();
}

function renderJobSearchProfilePicker() {
  renderProfileChoiceList({
    target: "#job-search-profile-picker-list",
    search: "#job-search-profile-picker-search",
    rows: jobSearchProfileRows,
    selectAttribute: "data-select-job-search-profile",
  });
}

function renderProfileChoiceList({ target, search, rows, selectAttribute }) {
  const el = $(target);
  if (!el) return;
  const keyword = String($(search)?.value || "").trim().toLowerCase();
  const filtered = rows.filter((profile) => {
    const structured = profile.structured_profile_json || {};
    return !keyword || [
      profile.id,
      profile.name,
      structured.name,
      profile.email,
      profile.headline,
      ...(profile.target_roles_json || structured.target_roles || []),
    ].join(" ").toLowerCase().includes(keyword);
  });
  if (!filtered.length) {
    el.innerHTML = `<article class="item meta">没有匹配的简历档案</article>`;
    return;
  }
  el.innerHTML = filtered.slice(0, 60).map((profile) => {
    const structured = profile.structured_profile_json || {};
    return `
      <article class="profile-picker-item">
        <div>
          <div class="item-title"><span>#${profile.id} ${escapeHtml(profile.name || structured.name || "未命名简历")}</span><span class="meta">${profile.source_type === "pdf" ? "PDF 上传" : "手动/自然语言"}</span></div>
          <div class="meta">${escapeHtml(profileSummaryText(profile))}</div>
          ${tags((structured.skills || []).slice(0, 6))}
        </div>
        <div class="profile-picker-actions">
          <button class="button primary" type="button" ${selectAttribute}="${profile.id}"><i data-lucide="check"></i> 选择</button>
          <a class="button ghost" href="/profiles/${profile.id}/html" target="_blank"><i data-lucide="eye"></i> 预览</a>
        </div>
      </article>
    `;
  }).join("");
  if (window.lucide) window.lucide.createIcons();
}

function jobSummaryText(job) {
  const structured = job?.structured_jd_json || {};
  const skills = structured.required_skills || structured.keywords || [];
  const location = job?.location ? ` · ${job.location}` : "";
  return `${job?.company || "未知公司"}${location}${skills.length ? ` · ${skills.slice(0, 3).join("、")}` : ""}`;
}

async function loadJobDetail() {
  const root = $("#job-detail-page");
  if (!root) return;
  const jobId = Number(root.dataset.jobId || 0);
  currentJobDetail = await api(`/jobs/${jobId}`);
  renderJobDetail(currentJobDetail);
  const params = new URLSearchParams(window.location.search);
  const sessionId = Number(params.get("session_id") || 0);
  const profileId = Number(params.get("profile_id") || 0);
  const back = $("#job-detail-back");
  if (back && sessionId) back.href = `/ui/jobs?session_id=${sessionId}`;
  const createLink = $("#job-detail-create-profile");
  if (createLink) createLink.href = `/ui/profiles?return_to=${encodeURIComponent(window.location.pathname + window.location.search)}`;
  if (profileId) {
    try {
      const profile = await api(`/profiles/${profileId}`);
      selectJobDetailProfile(profile);
    } catch (error) {
      toast(`简历档案无法加载：${error.message}`);
    }
  }
}

function renderJobDetail(job) {
  const structured = job.structured_jd_json || {};
  const header = $("#job-detail-header");
  if (header) {
    header.innerHTML = `
      <p class="eyebrow">岗位详情</p>
      <h1>${escapeHtml(job.title)}</h1>
      <div class="job-detail-meta">
        <span>${escapeHtml(job.company || "未知公司")}</span>
        <span>${escapeHtml(job.location || "地点未注明")}</span>
        <span>${escapeHtml(job.job_type || "类型未注明")}</span>
        <span>岗位 #${job.id}</span>
      </div>
    `;
  }
  const source = $("#job-detail-source");
  if (source) source.textContent = `${job.source || "manual"} · ${new Date(job.discovered_at).toLocaleDateString("zh-CN")}`;
  const tagSlot = $("#job-detail-tags");
  if (tagSlot) tagSlot.innerHTML = tags(structured.required_skills || structured.keywords || []);
  const sectionLabels = [
    ["responsibilities", "岗位职责"],
    ["qualifications", "任职要求"],
    ["required_skills", "必备技能"],
    ["preferred_skills", "加分项"],
  ];
  const structuredSlot = $("#job-detail-structured");
  if (structuredSlot) {
    structuredSlot.innerHTML = sectionLabels.map(([key, label]) => {
      const items = structured[key] || [];
      if (!items.length) return "";
      return `<section class="jd-section"><h2>${label}</h2><ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>`;
    }).join("") || `<section class="jd-section"><p class="meta">该岗位暂时没有结构化字段，请查看下方原始 JD。</p></section>`;
  }
  const raw = $("#job-detail-raw-jd");
  if (raw) raw.textContent = job.raw_jd_text || "";
  const applyLink = $("#job-apply-link");
  if (applyLink) {
    applyLink.hidden = !job.apply_url;
    if (job.apply_url) applyLink.href = job.apply_url;
  }
}

function selectJobDetailProfile(profile) {
  const structured = profile?.structured_profile_json || {};
  const title = $("#job-detail-profile-title");
  const summary = $("#job-detail-profile-summary");
  const input = $("#job-detail-profile-id");
  if (!profile) {
    if (input) input.value = "";
    if (title) title.textContent = "尚未选择简历";
    if (summary) summary.textContent = "你仍然可以只浏览 JD；需要分析或定制时再提供简历。";
    $("#run-job-match")?.setAttribute("disabled", "disabled");
    $("#run-job-tailor")?.setAttribute("disabled", "disabled");
    return;
  }
  if (input) input.value = profile.id;
  if (title) title.textContent = `#${profile.id} ${profile.name || structured.name || "未命名简历"}`;
  if (summary) summary.textContent = profileSummaryText(profile);
  $("#run-job-match")?.removeAttribute("disabled");
  $("#run-job-tailor")?.removeAttribute("disabled");
  const params = new URLSearchParams(window.location.search);
  params.set("profile_id", profile.id);
  window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
  const status = $("#job-detail-profile-status");
  if (status) {
    status.textContent = "已选择该简历，当前岗位的分析和定制都会使用它。";
    status.className = "field-hint ok";
  }
}

async function openJobDetailProfilePicker() {
  if (!jobDetailProfileRows.length) jobDetailProfileRows = await api("/profiles");
  renderJobDetailProfilePicker();
  openDialog("#job-detail-profile-picker-dialog");
  $("#job-detail-profile-picker-search")?.focus();
}

function renderJobDetailProfilePicker() {
  renderProfileChoiceList({
    target: "#job-detail-profile-picker-list",
    search: "#job-detail-profile-picker-search",
    rows: jobDetailProfileRows,
    selectAttribute: "data-select-job-detail-profile",
  });
}

function activateJobDetailTab(name) {
  document.querySelectorAll("[data-job-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.jobTab === name);
  });
  document.querySelectorAll("[data-job-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.jobPanel === name);
  });
}

function renderJobMatch(match) {
  const dimensions = match.dimension_scores_json || {};
  const evidence = match.relevant_evidence_json || [];
  return `
    <section class="job-match-analysis">
      <div class="job-match-score">
        <div>
          <p class="eyebrow">综合匹配</p>
          <strong>${escapeHtml(Math.round(match.overall_score))}</strong>
          <span>分</span>
        </div>
        <p>该分数用于比较岗位相关性，不代表录用概率。</p>
      </div>
      <div class="score-grid">
        ${Object.entries(dimensions).map(([key, value]) => `
          <div class="score-cell"><span>${escapeHtml(resumeReviewDimensionLabel(key))}</span><strong>${escapeHtml(Math.round(value))}</strong></div>
        `).join("")}
      </div>
      <div class="job-gap-grid">
        <section>
          <h3>已经匹配</h3>
          ${match.matched_skills_json?.length ? `<ul>${match.matched_skills_json.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<p class="meta">没有识别到明确命中的技能。</p>`}
        </section>
        <section>
          <h3>能力缺口</h3>
          ${match.missing_skills_json?.length ? `<ul>${match.missing_skills_json.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : `<p class="meta">没有识别到明显缺口。</p>`}
        </section>
      </div>
      ${match.suggestions_json?.length ? `<section class="job-match-suggestions"><h3>针对性建议</h3><ol>${match.suggestions_json.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ol></section>` : ""}
      ${evidence.length ? `
        <details class="review-evidence">
          <summary>查看简历证据（${evidence.length} 条）</summary>
          ${evidence.map((item) => `<article><strong>${escapeHtml(item.chunk_type || "经历证据")}</strong><p>${escapeHtml(item.text || item.snippet || "")}</p></article>`).join("")}
        </details>
      ` : ""}
    </section>
  `;
}

async function runJobDetailMatch() {
  const profileId = Number($("#job-detail-profile-id")?.value || 0);
  const jobId = Number($("#job-detail-page")?.dataset.jobId || 0);
  if (!profileId) throw new Error("请先选择简历档案。");
  activateJobDetailTab("match");
  const slot = $("#job-match-result");
  if (slot) slot.innerHTML = `<article class="item meta">正在从简历经历库检索与 JD 最相关的证据...</article>`;
  const match = await api("/matches", {
    method: "POST",
    body: JSON.stringify({ profile_id: profileId, job_id: jobId }),
  });
  if (slot) slot.innerHTML = renderJobMatch(match);
  if (window.lucide) window.lucide.createIcons();
  return match;
}

async function runJobDetailTailor() {
  const profileId = Number($("#job-detail-profile-id")?.value || 0);
  const jobId = Number($("#job-detail-page")?.dataset.jobId || 0);
  if (!profileId) throw new Error("请先选择简历档案。");
  activateJobDetailTab("tailor");
  const reviewSlot = $("#job-tailor-review");
  const resultSlot = $("#job-tailor-result");
  if (reviewSlot) reviewSlot.innerHTML = `<article class="item meta">正在进行岗位针对性评分和证据检查...</article>`;
  const review = await api(`/profiles/${profileId}/review`, {
    method: "POST",
    body: JSON.stringify({ job_id: jobId, include_llm: true }),
  });
  if (reviewSlot) reviewSlot.innerHTML = renderResumeReview(review);
  if (resultSlot) resultSlot.innerHTML = `<article class="item meta">评分完成，正在生成只包含简历正文的定制版本...</article>`;
  const version = await api("/resumes/tailor", {
    method: "POST",
    body: JSON.stringify({ profile_id: profileId, job_id: jobId }),
  });
  if (resultSlot) {
    resultSlot.innerHTML = `
      <article class="item validation-ok">
        <div class="item-title">
          <span>定制简历 #${version.id}</span>
          <span class="status-pill ${version.verification_json?.passed ? "ok" : "risk"}">${version.verification_json?.passed ? "事实检查通过" : "需要检查"}</span>
        </div>
        <p class="meta">评分和修改建议保留在当前页面，简历预览只包含可投递正文。</p>
        <div class="flow-result-actions">
          <a class="button primary" href="/resumes/${version.id}/html" target="_blank"><i data-lucide="eye"></i> 预览定制简历</a>
          <a class="button ghost" href="/ui/resumes"><i data-lucide="files"></i> 查看全部版本</a>
        </div>
      </article>
    `;
  }
  if (window.lucide) window.lucide.createIcons();
}

function updateTailorProfileCard(profile) {
  const form = $("#tailor-form");
  const title = $("#tailor-profile-title");
  const summary = $("#tailor-profile-summary");
  const card = $("#tailor-profile-card");
  if (!form || !title || !summary || !card) return;
  if (!profile) {
    form.profile_id.value = "";
    title.textContent = "尚未选择简历";
    summary.textContent = "选择用于定制的简历档案。";
    card.classList.remove("is-selected");
    return;
  }
  const structured = profile.structured_profile_json || {};
  form.profile_id.value = profile.id || "";
  title.textContent = `#${profile.id} ${profile.name || structured.name || "未命名简历"}`;
  summary.textContent = profileSummaryText(profile);
  card.classList.add("is-selected");
}

function updateTailorJobCard(job) {
  const form = $("#tailor-form");
  const title = $("#tailor-job-title");
  const summary = $("#tailor-job-summary");
  const card = $("#tailor-job-card");
  if (!form || !title || !summary || !card) return;
  if (!job) {
    form.job_id.value = "";
    title.textContent = "尚未选择岗位";
    summary.textContent = "选择岗位池中的 JD，用于评分和定制。";
    card.classList.remove("is-selected");
    return;
  }
  form.job_id.value = job.id || "";
  title.textContent = `#${job.id} ${job.title || "未命名岗位"}`;
  summary.textContent = jobSummaryText(job);
  card.classList.add("is-selected");
}

function closeDialog(selector) {
  const dialog = $(selector);
  if (!dialog) return;
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}

function openDialog(selector) {
  const dialog = $(selector);
  if (!dialog) return;
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "open");
}

async function openTailorProfilePicker() {
  if (!profilePickerRows.length) profilePickerRows = await api("/profiles");
  renderTailorProfilePickerList();
  openDialog("#tailor-profile-picker-dialog");
  $("#tailor-profile-picker-search")?.focus();
}

async function openTailorJobPicker() {
  if (!jobPickerRows.length) jobPickerRows = await api("/jobs");
  renderTailorJobPickerList();
  openDialog("#tailor-job-picker-dialog");
  $("#tailor-job-picker-search")?.focus();
}

function renderTailorProfilePickerList() {
  const el = $("#tailor-profile-picker-list");
  if (!el) return;
  const keyword = String($("#tailor-profile-picker-search")?.value || "").trim().toLowerCase();
  const rows = profilePickerRows.filter((profile) => {
    const structured = profile.structured_profile_json || {};
    const haystack = [profile.id, profile.name, structured.name, profile.email, structured.email, profile.headline, ...(profile.target_roles_json || structured.target_roles || [])]
      .join(" ")
      .toLowerCase();
    return !keyword || haystack.includes(keyword);
  });
  if (!rows.length) {
    el.innerHTML = `<article class="item meta">没有匹配的简历档案</article>`;
    return;
  }
  el.innerHTML = rows.slice(0, 60).map((profile) => {
    const structured = profile.structured_profile_json || {};
    const skills = (structured.skills || []).slice(0, 6);
    return `
      <article class="profile-picker-item">
        <div>
          <div class="item-title"><span>#${profile.id} ${escapeHtml(profile.name || structured.name || "未命名简历")}</span><span class="meta">${profile.source_type === "pdf" ? "PDF 上传" : "手动/自然语言"}</span></div>
          <div class="meta">${escapeHtml(profileSummaryText(profile))}</div>
          ${skills.length ? tags(skills) : ""}
        </div>
        <div class="profile-picker-actions">
          <button class="button primary" type="button" data-select-tailor-profile="${profile.id}"><i data-lucide="check"></i> 选择</button>
          <a class="button ghost" href="/profiles/${profile.id}/html" target="_blank"><i data-lucide="eye"></i> 预览</a>
        </div>
      </article>
    `;
  }).join("");
  if (window.lucide) window.lucide.createIcons();
}

function renderTailorJobPickerList() {
  const el = $("#tailor-job-picker-list");
  if (!el) return;
  const keyword = String($("#tailor-job-picker-search")?.value || "").trim().toLowerCase();
  const rows = jobPickerRows.filter((job) => {
    const structured = job.structured_jd_json || {};
    const haystack = [job.id, job.title, job.company, job.location, ...(structured.required_skills || []), ...(structured.keywords || [])]
      .join(" ")
      .toLowerCase();
    return !keyword || haystack.includes(keyword);
  });
  if (!rows.length) {
    el.innerHTML = `<article class="item meta">没有匹配的岗位</article>`;
    return;
  }
  el.innerHTML = rows.slice(0, 80).map((job) => {
    const structured = job.structured_jd_json || {};
    return `
      <article class="profile-picker-item">
        <div>
          <div class="item-title"><span>#${job.id} ${escapeHtml(job.title || "未命名岗位")}</span><span class="meta">${escapeHtml(job.company || "未知公司")}</span></div>
          <div class="meta">${escapeHtml(jobSummaryText(job))}</div>
          ${tags(structured.required_skills || structured.keywords || [])}
        </div>
        <div class="profile-picker-actions">
          <button class="button primary" type="button" data-select-tailor-job="${job.id}"><i data-lucide="check"></i> 选择</button>
          <a class="button ghost" href="/jobs/${job.id}/html" target="_blank"><i data-lucide="eye"></i> 预览 JD</a>
        </div>
      </article>
    `;
  }).join("");
  if (window.lucide) window.lucide.createIcons();
}

async function loadRuns(target = "#runs-list") {
  const rows = await api("/agent/runs");
  renderItems(target, rows, (row) => `
    <article class="item" data-run-card="${row.id}">
      <div class="item-title">
        <button class="ghost" data-run-id="${row.id}">#${row.id} ${escapeHtml(taskLabel(row.task_type))}</button>
        <span class="status-pill ${row.status === "completed" ? "ok" : row.status === "failed" ? "risk" : ""}">${row.status === "completed" ? "已完成" : row.status === "failed" ? "失败" : escapeHtml(row.status)}</span>
      </div>
      <div class="meta">简历 ${row.profile_id || "-"} · 岗位 ${row.job_id || "-"} · ${row.latency_ms}ms</div>
      ${renderRunBusinessGlance(row.output_json?.business_summary)}
      ${renderRunOutcomeLinks(row)}
    </article>
  `);
  document.querySelectorAll("[data-run-id]").forEach((button) => {
    button.addEventListener("click", () => loadRunSteps(button.dataset.runId));
  });
  if (target === "#runs-list" && rows.length) {
    await loadRunSteps(rows[0].id);
  }
}

function percentageText(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${Math.round(Number(value) * 100)}%`;
}

function approvalStatusLabel(value) {
  const labels = {
    pending: "等待确认",
    approved: "已批准",
    rejected: "已拒绝",
    missing: "缺少审批",
    not_required: "无需审批",
  };
  return labels[value] || value || "无需审批";
}

function renderRunBusinessGlance(summary) {
  if (!summary?.metrics) return "";
  const metrics = summary.metrics;
  const parts = [];
  if (metrics.match_score !== null && metrics.match_score !== undefined) parts.push(`匹配分 ${metrics.match_score}`);
  if (metrics.evidence_coverage !== null && metrics.evidence_coverage !== undefined) parts.push(`证据覆盖 ${percentageText(metrics.evidence_coverage)}`);
  if (metrics.guardrail_risk_level) parts.push(`事实风险 ${metrics.guardrail_risk_level}`);
  if (metrics.tool_call_count !== null && metrics.tool_call_count !== undefined) parts.push(`工具 ${metrics.tool_call_count} 次`);
  return parts.length ? `<div class="run-business-glance">${parts.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : "";
}

function businessSummaryHtml(summary, compact = false) {
  if (!summary?.metrics) {
    return `<p class="meta">运行完成后会在这里显示匹配、证据、事实检查、工具和审批结果。</p>`;
  }
  const metrics = summary.metrics;
  const selected = summary.selected_job || {};
  const sideEffect = summary.side_effect_layer || {};
  const risk = metrics.guardrail_risk_level || "未检查";
  const riskClass = risk === "high" || sideEffect.approval_bypass_detected ? "risk" : "ok";
  const metricItems = [
    ["岗位匹配分", metrics.match_score ?? "-"],
    ["证据覆盖", percentageText(metrics.evidence_coverage)],
    ["无依据表述", metrics.unsupported_claim_count ?? 0],
    ["已拦截问题", metrics.forbidden_claim_block_count ?? 0],
    ["工具调用", metrics.tool_call_count ?? 0],
    ["工具成功率", percentageText(metrics.tool_success_rate)],
    ["自动修复", metrics.repair_count ?? 0],
    ["审批", approvalStatusLabel(sideEffect.approval_status)],
  ];
  const visibleMetrics = compact ? metricItems.slice(0, 6) : metricItems;
  return `
    <div class="business-summary-head">
      <div>
        <strong>${escapeHtml(summary.headline || summary.task_label || "任务摘要")}</strong>
        <p class="meta">${escapeHtml([selected.company, selected.title].filter(Boolean).join(" · ") || summary.task_label || "")}</p>
      </div>
      <span class="status-pill ${riskClass}">事实风险 ${escapeHtml(risk)}</span>
    </div>
    <div class="business-summary-metrics">
      ${visibleMetrics.map(([label, value]) => `
        <div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
      `).join("")}
    </div>
    ${summary.result_layer?.missing_skills?.length ? `
      <div class="business-summary-detail"><span>待补能力</span>${tags(summary.result_layer.missing_skills.slice(0, 8))}</div>
    ` : ""}
    ${summary.skills_used?.length && !compact ? `
      <div class="business-summary-detail"><span>本次使用的能力</span>${tags(summary.skills_used)}</div>
    ` : ""}
    ${sideEffect.approval_bypass_detected ? `
      <p class="validation-risk">检测到高风险工具绕过审批，当前结果不可发布。</p>
    ` : ""}
  `;
}

function renderRunBusinessSummary(summary) {
  const el = $("#run-business-summary");
  if (!el) return;
  el.innerHTML = businessSummaryHtml(summary);
}

function renderRunOutcomeLinks(row) {
  const output = row.output_json || {};
  const links = [];
  const packageId = row.id;
  const resumeId = output.resume_version_id || output.tailor?.resume_version_id;
  const applicationId = output.application_id || output.application?.application_id;
  const prepId = output.interview_prep_id || output.interview_prep?.interview_prep_id;
  if (resumeId) links.push(packageAction("/ui/resumes", "file-check-2", "定制简历", packageId));
  if (applicationId) links.push(packageAction("/ui/applications", "send", "投递材料", packageId));
  if (prepId) links.push(packageAction("/ui/prep", "messages-square", "面试准备", packageId));
  if (output.matches?.length) links.push(`<a class="button ghost" href="/ui/jobs"><i data-lucide="briefcase-business"></i> 推荐岗位 ${output.matches.length} 个</a>`);
  if (output.requires_confirmation) links.push(`<button class="button primary" data-resume-run-id="${row.id}" type="button"><i data-lucide="check-circle-2"></i> 确认继续</button>`);
  return links.length ? `<div class="flow-result-actions">${links.join("")}</div>` : "";
}

async function loadRunSteps(runId) {
  const run = await api(`/agent/runs/${runId}`);
  document.querySelectorAll("[data-run-card]").forEach((card) => {
    card.classList.toggle("is-selected", Number(card.dataset.runCard) === Number(runId));
  });
  renderRunConfirmation(run);
  const [summary, rows] = await Promise.all([
    api(`/agent/runs/${runId}/summary`),
    api(`/agent/runs/${runId}/steps`),
  ]);
  renderRunBusinessSummary(summary);
  renderItems("#run-steps", rows, (row) => `
    <article class="item">
      <div class="item-title"><span>${escapeHtml(stepLabel(row.step_name))}</span><span class="status-pill ${row.status === "completed" ? "ok" : row.status === "failed" ? "risk" : ""}">${row.status === "completed" ? "完成" : row.status === "failed" ? "失败" : escapeHtml(row.status)}</span></div>
      <div class="meta">${row.latency_ms}ms${row.error_message ? ` · ${escapeHtml(row.error_message)}` : ""}</div>
    </article>
  `);
  await loadRunEvents(runId);
  subscribeAgentRunEvents(runId);
}

function renderRunConfirmation(run) {
  const el = $("#run-confirmation");
  if (!el) return;
  const output = run.output_json || {};
  const needsConfirmation = run.status === "waiting_for_confirmation" || output.requires_confirmation;
  if (!needsConfirmation) {
    el.innerHTML = `
      <article class="item meta">
        <strong>无待确认事项</strong>
        <p>当前运行不需要人工确认。只有投递包、浏览器填写、邮件草稿/发送等高风险动作，才会在这里要求你确认。</p>
      </article>
    `;
    return;
  }
  el.innerHTML = `
    <article class="item validation-risk">
      <div class="item-title">
        <span>待确认事项</span>
        <span class="status-pill risk">人工确认</span>
      </div>
      <p class="meta">这是高风险动作的确认点，不是普通历史操作。当前 run 准备生成或继续投递相关材料，需要你确认后才会继续执行。</p>
      <div class="flow-result-actions">
        <button class="button primary" data-resume-run-id="${run.id}" type="button"><i data-lucide="check-circle-2"></i> 确认继续</button>
      </div>
    </article>
  `;
  if (window.lucide) window.lucide.createIcons();
}

function eventLabel(eventType, nodeName = "") {
  const labels = {
    run_created: "创建流程",
    run_started: "开始运行",
    run_resumed: "恢复运行",
    run_finished: "流程结束",
    run_closed: "连接关闭",
    graph_started: "图开始",
    graph_completed: "图完成",
    graph_update: "图更新",
    graph_interrupt: "等待确认",
    graph_node_started: "节点开始",
    graph_node_update: "节点更新",
    graph_node_completed: "节点完成",
    graph_failed: "图失败",
    step_started: "步骤开始",
    step_completed: "步骤完成",
    step_failed: "步骤失败",
    artifact_created: "产物生成",
    queue_enqueue_failed: "队列入队失败",
    heartbeat: "保持连接",
  };
  const base = labels[eventType] || eventType;
  return nodeName ? `${base}：${stepLabel(nodeName)}` : base;
}

function renderRunEvents(events) {
  const el = $("#run-events");
  if (!el) return;
  if (!events.length) {
    el.innerHTML = `<article class="event-row"><strong>暂无事件</strong><small>流程开始后会显示 LangGraph 节点进度</small></article>`;
    return;
  }
  el.innerHTML = events.slice(-80).map((event) => {
    const type = event.event_type || event.type || "";
    const node = event.node_name || event.event_json?.node_name || "";
    const statusClass = type.includes("failed") ? "failed" : type.includes("started") || type.includes("interrupt") ? "running" : "";
    const time = event.created_at ? new Date(event.created_at).toLocaleTimeString() : "";
    const detail = event.event_json?.error || event.event_json?.status || event.event_json?.artifact_type || event.event_json?.tool_name || "";
    return `
      <article class="event-row ${statusClass}" data-event-id="${escapeHtml(event.id || "")}">
        <strong>${escapeHtml(eventLabel(type, node))}</strong>
        <small>${escapeHtml(time)}${detail ? ` · ${escapeHtml(detail)}` : ""}</small>
      </article>
    `;
  }).join("");
}

async function loadRunEvents(runId) {
  const events = await api(`/agent/runs/${runId}/events`);
  renderRunEvents(events);
  return events;
}

function subscribeAgentRunEvents(runId, callbacks = {}) {
  if (!$("#run-events") && !callbacks.onEvent) return null;
  if (typeof EventSource === "undefined") {
    return null;
  }
  if (activeRunEventSource) activeRunEventSource.close();
  const source = new EventSource(`/agent/runs/${runId}/events/stream`);
  activeRunEventSource = source;
  const events = [];
  const push = (event) => {
    if (event.event_type !== "heartbeat") {
      events.push(event);
      if ($("#run-events")) renderRunEvents(events);
      callbacks.onEvent?.(event);
    }
  };
  source.onmessage = (message) => {
    try {
      push(JSON.parse(message.data));
    } catch (_) {
      // Ignore malformed keepalive frames.
    }
  };
  [
    "run_created",
    "run_started",
    "run_resumed",
    "run_finished",
    "run_closed",
    "graph_started",
    "graph_completed",
    "graph_update",
    "graph_interrupt",
    "graph_node_started",
    "graph_node_update",
    "graph_node_completed",
    "graph_failed",
    "step_started",
    "step_completed",
    "step_failed",
    "artifact_created",
    "queue_enqueue_failed",
    "heartbeat",
  ].forEach((type) => {
    source.addEventListener(type, (message) => {
      try {
        const payload = JSON.parse(message.data);
        push({ ...payload, event_type: payload.event_type || type });
        if (type === "run_finished" || type === "run_closed" || type === "graph_interrupt" || type === "graph_failed") {
          callbacks.onTerminal?.(payload, type);
          if (type !== "graph_interrupt") source.close();
        }
      } catch (_) {
        // Ignore malformed frames.
      }
    });
  });
  source.onerror = () => {
    callbacks.onError?.();
    source.close();
  };
  return source;
}

async function loadRecentRuns() {
  const rows = await api("/agent/runs");
  const el = $("#recent-runs");
  if (!el) return;
  el.innerHTML = `
    <table>
      <thead><tr><th>ID</th><th>任务</th><th>状态</th><th>耗时</th></tr></thead>
      <tbody>${rows.slice(0, 8).map((row) => `
        <tr><td>${row.id}</td><td>${escapeHtml(row.task_type)}</td><td>${escapeHtml(row.status)}</td><td>${row.latency_ms}ms</td></tr>
      `).join("")}</tbody>
    </table>
  `;
}

async function loadResumes() {
  const rows = await api("/resumes");
  const el = $("#resumes-list");
  if (!el) return;
  if (!rows.length) {
    el.innerHTML = `<div class="item meta">暂无定制版本</div>`;
    return;
  }
  el.innerHTML = rows.map((row, index) => `
    <article class="resume-card">
      <div class="item-title"><span>#${row.id} ${escapeHtml(row.title)}</span><span class="status-pill ${row.verification_json.passed ? "ok" : "risk"}">${row.verification_json.passed ? "事实检查通过" : "需检查"}</span></div>
      <p class="meta">简历 ${row.profile_id} · 岗位 ${row.job_id}</p>
      ${index < 3
        ? `<iframe class="resume-preview-frame" src="/resumes/${row.id}/html" loading="lazy" title="定制简历 #${row.id} 预览"></iframe>`
        : `<div class="resume-preview-placeholder">点击下方按钮打开 HTML 预览</div>`}
      <div class="flow-result-actions">
        <a class="button ghost" href="/resumes/${row.id}/html" target="_blank"><i data-lucide="eye"></i> 打开 HTML 预览</a>
        <a class="button ghost" href="/resumes/${row.id}/markdown"><i data-lucide="download"></i> 下载 Markdown</a>
      </div>
      ${renderTailoredResumeDiagnostics(row)}
    </article>
  `).join("");
  if (window.lucide) window.lucide.createIcons();
}

function renderTailoredResumeDiagnostics(row) {
  const changes = row.change_summary_json || [];
  const keywords = row.keyword_alignment_json || {};
  const evidence = row.source_evidence_json || [];
  const verification = row.verification_json || {};
  return `
    <details class="tailor-diagnostics">
      <summary>查看检查、修改说明和证据</summary>
      <div class="review-block">
        <h3>事实检查</h3>
        <p>${verification.passed ? "事实边界检查通过。" : "存在需要人工检查的风险。"}${verification.risk_level ? ` 风险：${escapeHtml(verification.risk_level)}` : ""}</p>
      </div>
      ${changes.length ? `<div class="review-block"><h3>修改说明</h3><ul>${changes.map((item) => `<li><strong>${escapeHtml(item.section || "简历")}</strong>：${escapeHtml(item.change || item.reason || JSON.stringify(item))}</li>`).join("")}</ul></div>` : ""}
      ${Object.keys(keywords).length ? `<div class="review-block"><h3>关键词对齐</h3><pre>${escapeHtml(JSON.stringify(keywords, null, 2))}</pre></div>` : ""}
      ${evidence.length ? `<div class="review-block"><h3>证据来源</h3><ul>${evidence.slice(0, 8).map((item) => `<li>${escapeHtml(item.text || item.snippet || JSON.stringify(item))}</li>`).join("")}</ul></div>` : ""}
    </details>
  `;
}

async function loadApplications() {
  const rows = await api("/applications");
  renderItems("#applications-list", rows, (row) => {
    const packageId = userPackageId(row);
    return `
    <article class="item application-card">
      <div class="item-title"><span>${escapeHtml(packageLabel(packageId))} · 岗位 ${escapeHtml(row.job_id)}</span><span class="status-pill ${row.status === "ready" ? "ok" : ""}">${row.status === "ready" ? "准备好了" : escapeHtml(row.status)}</span></div>
      <div class="meta">投递材料 · 定制简历 ${row.resume_version_id || "-"}</div>
      ${row.apply_url ? `<p><a class="button ghost" href="${escapeHtml(row.apply_url)}" target="_blank"><i data-lucide="external-link"></i> 打开投递页</a></p>` : ""}
      ${applicationValidation(row)}
      ${row.outreach_message ? `<p class="message-preview">${escapeHtml(row.outreach_message)}</p>` : ""}
      <pre class="application-letter">${escapeHtml(row.cover_letter || "")}</pre>
    </article>
  `;
  });
}

async function loadInterviewPreps() {
  const rows = await api("/interview-prep");
  renderItems("#interview-prep-list", rows, (row) => {
    const summary = row.summary_json || {};
    const coverage = row.coverage_json || {};
    const preparationAngles = summary.preparation_angles || preparationAnglesFromCoverage(coverage);
    const referenceLinks = summary.interview_reference_links || [];
    const questionSets = row.question_sets_json || [];
    const drills = row.gap_drills_json || [];
    const research = row.research_checklist_json || [];
    return `
      <article class="item">
        <div class="item-title">
          <span>#${row.id} ${escapeHtml(row.title)}</span>
          <span class="status-pill ${coverage.passed ? "ok" : ""}">${coverage.passed ? "可开始练习" : "需补充"}</span>
        </div>
        <div class="meta">简历 ${row.profile_id} · 岗位 ${row.job_id} · ${escapeHtml(summary.fit_level || "匹配度未知")} · ${escapeHtml(summary.overall_score ?? "-")} 分</div>
        <p><a class="button ghost" href="/interview-prep/${row.id}/markdown"><i data-lucide="download"></i> Markdown</a></p>
        <div class="summary-strip">
          <span><strong>${coverage.question_count || countInterviewQuestions(questionSets)}</strong> 道题</span>
          <span><strong>${drills.length}</strong> 个缺口练习</span>
          <span><strong>${referenceLinks.length}</strong> 条参考链接</span>
        </div>
        ${renderPreparationAngles(preparationAngles)}
        ${renderInterviewReferenceLinks(referenceLinks)}
        ${questionSets.map((group) => `
          <h3>${escapeHtml(group.category)}</h3>
          <ul class="compact-list">${(group.questions || []).slice(0, 4).map((q) => `
            <li data-question-id="${escapeHtml(q.question_id || "")}">
              <span class="tag">${escapeHtml(q.question_id || "-")}</span>
              <span class="tag">${escapeHtml(interviewAngleLabel(q.preparation_angle))}</span>
              <span class="tag">${escapeHtml(interviewSourceLabel(q.source_perspective))}</span>
              <span class="tag">${escapeHtml(q.risk_level || "low")}</span>
              ${escapeHtml(q.question)}
              ${(q.follow_ups || []).length ? `<div class="meta">追问：${escapeHtml((q.follow_ups || []).slice(0, 2).join(" / "))}</div>` : ""}
            </li>
          `).join("")}</ul>
        `).join("")}
        ${drills.length ? `<h3>缺口 Drill</h3><ul class="compact-list">${drills.slice(0, 5).map((item) => `<li><span class="tag">${escapeHtml(item.skill)}</span>${escapeHtml(item.honest_strategy)}</li>`).join("")}</ul>` : ""}
        ${research.length ? `<h3>外部调研清单</h3><ul class="compact-list">${research.map((item) => `<li><span class="tag">${escapeHtml(item.site || item.topic)}</span>${escapeHtml(item.topic)}：${escapeHtml(item.query)}</li>`).join("")}</ul>` : ""}
      </article>
    `;
  });
}

function countInterviewQuestions(questionSets) {
  return (questionSets || []).reduce((total, group) => total + ((group.questions || []).length), 0);
}

function renderPreparationAngles(angles) {
  if (!angles || !angles.length) return "";
  return `
    <h3>准备角度</h3>
    <ul class="compact-list">${angles.map((angle) => {
      const inputs = (angle.source_inputs || []).filter(Boolean).slice(0, 2).join("；");
      const focus = (angle.focus || []).filter(Boolean).slice(0, 1).join("；");
      return `
        <li>
          <span class="tag">${escapeHtml(angle.label || interviewAngleLabel(angle.angle))}</span>
          <span class="tag">${escapeHtml(angle.question_count || 0)} 题</span>
          ${escapeHtml(inputs || focus || "")}
        </li>
      `;
    }).join("")}</ul>
  `;
}

function renderQuestionQuality(quality) {
  if (!quality || Object.keys(quality).length === 0) return "";
  const rates = quality.rates || {};
  const issueCounts = quality.issue_counts || {};
  const issues = quality.sample_issues || [];
  const metricRows = [
    ["JD 贴合", rates.jd_alignment],
    ["连续追问", rates.follow_up_depth],
    ["缺口边界", rates.gap_boundary],
    ["项目绑定", rates.project_binding],
    ["证据追溯", rates.evidence_traceability],
    ["行动性", rates.actionability],
    ["重复率", rates.duplicate_rate],
  ];
  return `
    <h3>题目质量</h3>
    <div class="validation-panel ${quality.passed ? "validation-ok" : "validation-risk"}">
      <div class="validation-head">
        <span class="status-pill ${quality.passed ? "ok" : ""}">${quality.passed ? "通过" : "待检查"}</span>
        <span class="meta">质量分 ${formatPercent(quality.score)}</span>
      </div>
      <div class="validation-grid">
        ${metricRows.map(([label, value]) => `<div><strong>${escapeHtml(label)}</strong><div class="meta">${formatPercent(value)}</div></div>`).join("")}
      </div>
      ${Object.keys(issueCounts).length ? `<div class="meta">失败项：${escapeHtml(Object.entries(issueCounts).map(([key, value]) => `${key}=${value}`).join("，"))}</div>` : ""}
      ${issues.length ? `<ul class="compact-list">${issues.slice(0, 6).map(renderQualityIssue).join("")}</ul>` : ""}
    </div>
  `;
}

function renderQualityIssue(issue) {
  const text = String(issue || "");
  const match = text.match(/^(q\d{2}_\d{2}|[A-Za-z0-9_-]+):\s*(.*)$/);
  if (!match) return `<li>${escapeHtml(text)}</li>`;
  return `
    <li>
      <button type="button" class="inline-action" data-quality-jump="${escapeHtml(match[1])}">${escapeHtml(match[1])}</button>
      ${escapeHtml(match[2] || "")}
    </li>
  `;
}

function renderInterviewReferenceLinks(links) {
  if (!links || !links.length) return "";
  return `
    <h3>面经参考链接</h3>
    <ul class="compact-list">${links.slice(0, 8).map((item) => `
      <li>
        <span class="tag">${escapeHtml(item.site || item.kind || "参考")}</span>
        ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank">${escapeHtml(item.title || item.url)}</a>` : escapeHtml(item.title || item.query || "")}
        <div class="meta">${escapeHtml(item.note || item.query || "")}</div>
      </li>
    `).join("")}</ul>
  `;
}

function preparationAnglesFromCoverage(coverage) {
  const counts = coverage.preparation_angle_counts || {};
  const labels = coverage.preparation_angle_labels || {};
  return Object.keys(counts).map((angle) => ({
    angle,
    label: labels[angle] || interviewAngleLabel(angle),
    question_count: counts[angle] || 0,
  }));
}

function questionQualityFromCoverage(coverage) {
  if (!coverage || coverage.question_quality_score === undefined) return {};
  return {
    passed: coverage.question_quality_passed,
    score: coverage.question_quality_score,
    rates: coverage.question_quality_rates || {},
    mode: "coverage",
  };
}

function formatPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${Math.round(number * 100)}%`;
}

function safeJson(value, limit = 1800) {
  const text = JSON.stringify(value || {}, null, 2);
  if (text.length <= limit) return text;
  return `${text.slice(0, limit)}\n... truncated`;
}

function statusClass(value) {
  const normalized = String(value || "").toLowerCase();
  if (["ok", "ready", "completed", "true"].includes(normalized)) return "ok";
  if (normalized.includes("fail") || normalized.includes("missing") || normalized.includes("degraded")) return "risk";
  return "";
}

function renderCheckGrid(checks) {
  return `<div class="validation-grid">${Object.entries(checks || {}).map(([key, value]) => `
    <div>
      <strong>${escapeHtml(key)}</strong>
      <div class="meta"><span class="status-pill ${statusClass(value)}">${escapeHtml(value)}</span></div>
    </div>
  `).join("")}</div>`;
}

function renderOpsReadiness(readiness) {
  return `
    <article class="item">
      <div class="item-title">
        <span>服务状态</span>
        <span class="status-pill ${statusClass(readiness.status)}">${escapeHtml(readiness.status || "unknown")}</span>
      </div>
      <div class="validation-panel ${readiness.status === "ready" ? "validation-ok" : "validation-risk"}">
        ${renderCheckGrid(readiness.checks || {})}
      </div>
    </article>
  `;
}

function renderOpsMetrics(metrics) {
  const app = metrics.app || {};
  const database = metrics.database || {};
  const latest = metrics.latest_evaluation || {};
  const summary = latest.summary || {};
  return `
    <article class="item">
      <div class="validation-grid">
        ${metricCell("请求数", app.request_count ?? 0)}
        ${metricCell("平均延迟", `${app.avg_latency_ms ?? 0}ms`)}
        ${metricCell("Agent Runs", Object.values(database.agent_runs_by_status || {}).reduce((a, b) => a + b, 0))}
        ${metricCell("后台任务", Object.values(database.tasks_by_status || {}).reduce((a, b) => a + b, 0))}
        ${metricCell("LLM 调用", Object.values(database.llm_calls_by_status || {}).reduce((a, b) => a + b, 0))}
        ${metricCell("评测次数", database.evaluation_run_count ?? 0)}
      </div>
      ${latest.id ? `<p class="meta">最近评测 #${escapeHtml(latest.id)} / ${escapeHtml(summary.evaluation_type || latest.name)} / ${escapeHtml(summary.status || "-")}</p>` : ""}
      <details class="details-block">
        <summary>状态分布</summary>
        <pre>${escapeHtml(safeJson({
          status_counts: app.status_counts,
          top_routes: app.top_routes,
          agent_runs_by_status: database.agent_runs_by_status,
          tasks_by_status: database.tasks_by_status,
          llm_calls_by_status: database.llm_calls_by_status,
        }))}</pre>
      </details>
    </article>
  `;
}

function renderOpsConfig(config) {
  const llm = config.llm || {};
  const retrieval = config.retrieval || {};
  const security = config.security || {};
  const queue = config.queue || {};
  return `
    <article class="item">
      <div class="validation-grid">
        ${metricCell("环境", config.app_env || "-")}
        ${metricCell("数据库", config.database_backend || "-")}
        ${metricCell("LLM", llm.configured ? "已配置" : "未配置")}
        ${metricCell("模型", llm.model || "-")}
        ${metricCell("Thinking", llm.thinking_mode || "-")}
        ${metricCell("Embedding", retrieval.embedding_provider || "-")}
        ${metricCell("Reranker", retrieval.reranker_enabled ? retrieval.reranker_provider : "disabled")}
        ${metricCell("写权限保护", security.require_admin_for_mutations ? "已开启" : "未开启")}
        ${metricCell("RBAC", security.rbac_enabled ? "已开启" : "未开启")}
        ${metricCell("Redis", queue.redis_mode || "-")}
        ${metricCell("Worker 并发", queue.worker_concurrency ?? "-")}
      </div>
      <details class="details-block">
        <summary>完整脱敏配置</summary>
        <pre>${escapeHtml(safeJson(config))}</pre>
      </details>
    </article>
  `;
}

function renderQueueStatus(queue) {
  const deadLetters = queue.dead_letter_preview || [];
  if (queue.status === "disabled" || queue.redis_enabled === false) {
    return `
      <article class="item">
        <div class="item-title"><span>Redis 队列未启用</span><span class="status-pill">disabled</span></div>
        <p class="meta">${escapeHtml(queue.message || "后台队列需要 Redis 服务和 worker 进程。")}</p>
      </article>
    `;
  }
  return `
    <article class="item">
      <div class="validation-grid">
        ${metricCell("队列", queue.queue_name || "-")}
        ${metricCell("queued", queue.queued_count ?? "-")}
        ${metricCell("DLQ", queue.dead_letter_count ?? "-")}
        ${metricCell("最大重试", queue.worker_max_attempts ?? "-")}
        ${metricCell("high", queue.queued_by_priority?.high ?? 0)}
        ${metricCell("normal", queue.queued_by_priority?.normal ?? 0)}
        ${metricCell("low", queue.queued_by_priority?.low ?? 0)}
      </div>
      ${deadLetters.length ? `
        <div class="result-list compact-list">
          ${deadLetters.map((item) => `
            <article class="item validation-risk">
              <div class="item-title">
                <span>DLQ #${item.dlq_index ?? "-"} ${escapeHtml(item.kind || "unknown")}</span>
                <span class="status-pill risk">${escapeHtml(String(item.attempts ?? "-"))} 次</span>
              </div>
              <div class="meta">run=${escapeHtml(String(item.run_id || "-"))} / error=${escapeHtml(String(item.last_error || item.raw_payload || "-")).slice(0, 180)}</div>
              <details class="details-block">
                <summary>Payload</summary>
                <pre>${escapeHtml(safeJson(item))}</pre>
              </details>
              <div class="flow-result-actions">
                <button class="button primary" data-dlq-replay="${item.dlq_index}" type="button"><i data-lucide="rotate-ccw"></i> 重放</button>
                <button class="button ghost" data-dlq-discard="${item.dlq_index}" type="button"><i data-lucide="trash-2"></i> 丢弃</button>
              </div>
            </article>
          `).join("")}
        </div>
      ` : `<p class="meta">Dead-letter queue 暂无异常 payload。</p>`}
    </article>
  `;
}

function renderOpsAgentRuns(runs) {
  const active = (runs || []).filter((run) => ["queued", "running", "waiting_for_confirmation"].includes(run.status)).slice(0, 12);
  if (!active.length) return `<div class="item meta">暂无进行中的 Agent run</div>`;
  return active.map((run) => `
    <article class="item">
      <div class="item-title">
        <span>#${run.id} ${escapeHtml(taskLabel(run.task_type))}</span>
        <span class="status-pill ${statusClass(run.status)}">${escapeHtml(run.status)}</span>
      </div>
      <div class="meta">Profile ${run.profile_id || "-"} / Job ${run.job_id || "-"} / ${escapeHtml(run.created_at || "")}</div>
      <div class="flow-result-actions">
        <a class="button ghost" href="/ui/agent-runs"><i data-lucide="route"></i> 查看 trace</a>
        <button class="button ghost" data-cancel-run="${run.id}" type="button"><i data-lucide="circle-x"></i> 取消</button>
      </div>
    </article>
  `).join("");
}

function renderApprovalList(rows) {
  if (!rows || !rows.length) return `<div class="item meta">暂无审批记录</div>`;
  return rows.map((row) => `
    <article class="item">
      <div class="item-title">
        <span>#${row.id} ${escapeHtml(row.action_type)}</span>
        <span class="status-pill ${statusClass(row.status)}">${escapeHtml(row.status)}</span>
      </div>
      <div class="meta">Run #${row.run_id} / hash=${escapeHtml(String(row.payload_hash || "").slice(0, 12))} / ${escapeHtml(row.created_at || "")}</div>
      <details class="details-block">
        <summary>审批摘要</summary>
        <pre>${escapeHtml(safeJson(row.payload_summary_json || {}))}</pre>
      </details>
      ${row.status === "pending" ? `
        <div class="flow-result-actions">
          <button class="button primary" data-approval-decision="${row.id}" data-approved="true" type="button"><i data-lucide="check-circle-2"></i> 通过</button>
          <button class="button ghost" data-approval-decision="${row.id}" data-approved="false" type="button"><i data-lucide="x-circle"></i> 拒绝</button>
        </div>
      ` : row.note ? `<p class="meta">备注：${escapeHtml(row.note)}</p>` : ""}
    </article>
  `).join("");
}

function renderStaleRuns(payload) {
  const rows = payload?.stale_runs || [];
  if (!rows.length) return `<div class="item meta">暂无 stale running run</div>`;
  return rows.map((row) => `
    <article class="item validation-risk">
      <div class="item-title">
        <span>Run #${row.run_id} ${escapeHtml(row.task_type || "")}</span>
        <span class="status-pill risk">stale</span>
      </div>
      <div class="meta">最后阶段：${escapeHtml(row.last_stage || row.last_event_type || "-")} / ${escapeHtml(row.last_event_at || "")}</div>
    </article>
  `).join("");
}

function renderOpsAuditEvents(rows) {
  if (!rows || !rows.length) return `<div class="item meta">暂无运维审计事件</div>`;
  return rows.slice(0, 20).map((row) => `
    <article class="item">
      <div class="item-title">
        <span>#${row.id} ${escapeHtml(row.event_type)}</span>
        <span class="status-pill neutral">${escapeHtml(row.target_type || "-")}</span>
      </div>
      <div class="meta">actor=${escapeHtml(row.actor || "-")} / target=${escapeHtml(row.target_id || "-")} / ${escapeHtml(row.created_at || "")}</div>
      <details class="details-block">
        <summary>审计 payload</summary>
        <pre>${escapeHtml(safeJson(row.payload_json || {}))}</pre>
      </details>
    </article>
  `).join("");
}

function renderOpsLogs(logs) {
  if (!logs || !logs.length) return `<div class="item meta">暂无 LLM 调用日志</div>`;
  return logs.slice(0, 12).map((row) => `
    <article class="item">
      <div class="item-title">
        <span>${escapeHtml(row.trace_name)}</span>
        <span class="status-pill ${statusClass(row.status)}">${escapeHtml(row.status)}</span>
      </div>
      <div class="meta">${escapeHtml(row.model)} / ${escapeHtml(row.latency_ms)}ms / stage=${escapeHtml(row.context_json?.stage || "-")}</div>
      ${row.error_message ? `<div class="message-preview">${escapeHtml(row.error_message)}</div>` : ""}
      ${row.response_preview ? `<details class="details-block"><summary>响应预览</summary><pre>${escapeHtml(row.response_preview)}</pre></details>` : ""}
    </article>
  `).join("");
}

async function loadDashboardOpsSummary() {
  const target = $("#dashboard-ops-summary");
  const taskTarget = $("#dashboard-task-summary");
  if (!target && !taskTarget) return;
  const [readiness, metrics, tasks] = await Promise.all([
    api("/ops/readiness"),
    api("/ops/metrics"),
    api("/tasks?limit=3"),
  ]);
  if (target) {
    const latest = metrics.latest_evaluation?.summary || {};
    target.innerHTML = `
      ${renderOpsReadiness(readiness)}
      <article class="item">
        <div class="validation-grid">
          ${metricCell("请求数", metrics.app?.request_count ?? 0)}
          ${metricCell("平均延迟", `${metrics.app?.avg_latency_ms ?? 0}ms`)}
          ${metricCell("最近评测", latest.status || "-")}
          ${metricCell("LLM 调用", Object.values(metrics.database?.llm_calls_by_status || {}).reduce((a, b) => a + b, 0))}
        </div>
      </article>
    `;
  }
  if (taskTarget) {
    taskTarget.innerHTML = tasks.length ? tasks.map(renderTaskRun).join("") : `<div class="item meta">暂无后台任务</div>`;
  }
  if (window.lucide) window.lucide.createIcons();
}

async function loadOpsPage() {
  if (!$("#ops-readiness")) return;
  updateAdminTokenState();
  const results = await Promise.allSettled([
    api("/ops/readiness"),
    api("/ops/metrics"),
    api("/ops/config"),
    api("/tasks?limit=12"),
    api("/llm/debug/logs?limit=20"),
    api("/ops/queue/status"),
    api("/agent/runs"),
    api("/ops/approvals?limit=20"),
    api("/ops/agent-runs/stale"),
    api("/ops/audit-events?limit=20"),
  ]);
  const [readiness, metrics, config, tasks, logs, queue, runs, approvals, staleRuns, auditEvents] = results;
  $("#ops-readiness").innerHTML = readiness.status === "fulfilled"
    ? renderOpsReadiness(readiness.value)
    : `<div class="item validation-risk">${escapeHtml(readiness.reason.message)}</div>`;
  $("#ops-metrics").innerHTML = metrics.status === "fulfilled"
    ? renderOpsMetrics(metrics.value)
    : `<div class="item validation-risk">${escapeHtml(metrics.reason.message)}</div>`;
  $("#ops-config").innerHTML = config.status === "fulfilled"
    ? renderOpsConfig(config.value)
    : `<div class="item validation-risk">${escapeHtml(config.reason.message)}</div>`;
  $("#ops-tasks").innerHTML = tasks.status === "fulfilled" && tasks.value.length
    ? tasks.value.map(renderTaskRun).join("")
    : `<div class="item meta">${tasks.status === "fulfilled" ? "暂无后台任务" : escapeHtml(tasks.reason.message)}</div>`;
  $("#ops-llm-logs").innerHTML = logs.status === "fulfilled"
    ? renderOpsLogs(logs.value)
    : `<div class="item validation-risk">${escapeHtml(logs.reason.message)}</div>`;
  if ($("#ops-queue")) {
    $("#ops-queue").innerHTML = queue.status === "fulfilled"
      ? renderQueueStatus(queue.value)
      : `<div class="item validation-risk">${escapeHtml(queue.reason.message)}</div>`;
  }
  if ($("#ops-agent-runs")) {
    $("#ops-agent-runs").innerHTML = runs.status === "fulfilled"
      ? renderOpsAgentRuns(runs.value)
      : `<div class="item validation-risk">${escapeHtml(runs.reason.message)}</div>`;
  }
  if ($("#ops-approvals")) {
    $("#ops-approvals").innerHTML = approvals.status === "fulfilled"
      ? renderApprovalList(approvals.value)
      : `<div class="item validation-risk">${escapeHtml(approvals.reason.message)}</div>`;
  }
  if ($("#ops-stale-runs")) {
    $("#ops-stale-runs").innerHTML = staleRuns.status === "fulfilled"
      ? renderStaleRuns(staleRuns.value)
      : `<div class="item validation-risk">${escapeHtml(staleRuns.reason.message)}</div>`;
  }
  if ($("#ops-audit-events")) {
    $("#ops-audit-events").innerHTML = auditEvents.status === "fulfilled"
      ? renderOpsAuditEvents(auditEvents.value)
      : `<div class="item validation-risk">${escapeHtml(auditEvents.reason.message)}</div>`;
  }
  if (window.lucide) window.lucide.createIcons();
}

function updateAdminTokenState() {
  const state = $("#admin-token-state");
  const form = $("#admin-token-form");
  const token = getAdminToken();
  if (form?.admin_token) form.admin_token.value = token;
  if (state) state.textContent = token ? "已保存到本机浏览器，后续请求会自动带上 X-Admin-Token。" : "未保存管理令牌；如果服务端开启写权限保护，写操作会返回 401。";
}

function focusInterviewQuestion(button) {
  const questionId = button.dataset.qualityJump;
  if (!questionId) return;
  const container = button.closest(".item");
  const target = Array.from(container?.querySelectorAll("[data-question-id]") || [])
    .find((node) => node.dataset.questionId === questionId);
  if (!target) {
    toast(`当前预览未显示 ${questionId}，请打开 Markdown 查看完整题目`);
    return;
  }
  container.querySelectorAll(".question-highlight").forEach((node) => node.classList.remove("question-highlight"));
  target.classList.add("question-highlight");
  target.scrollIntoView({ behavior: "smooth", block: "center" });
}

const CAREER_FLOW_STAGES = ["profile", "search", "match", "results", "tailor", "apply", "interview"];

function userPackageId(value) {
  const key = value?.idempotency_key || value?.output_json?.idempotency_key || "";
  const match = String(key).match(/^agent_run:(\d+):/);
  return match ? match[1] : value?.package_id || value?.run_id || value?.id || "";
}

function packageLabel(packageId) {
  return packageId ? `求职包 #${packageId}` : "本次求职包";
}

function packageAction(href, icon, label, packageId) {
  return `<a class="button ghost" href="${escapeHtml(href)}"><i data-lucide="${escapeHtml(icon)}"></i> ${escapeHtml(label)}${packageId ? ` · ${escapeHtml(packageLabel(packageId))}` : ""}</a>`;
}

function pushUniqueAction(actions, seen, key, html) {
  if (!key || seen.has(key)) return;
  seen.add(key);
  actions.push(html);
}

function readActiveRuns() {
  try {
    const rows = JSON.parse(window.localStorage?.getItem(ACTIVE_RUN_KEY) || "[]");
    return Array.isArray(rows)
      ? rows.filter((row) => Number(row.run_id || row.id) > 0)
      : [];
  } catch (_) {
    return [];
  }
}

function readDismissedRunIds() {
  try {
    const rows = JSON.parse(window.localStorage?.getItem(DISMISSED_RUN_KEY) || "[]");
    return new Set(Array.isArray(rows) ? rows.map((id) => Number(id)).filter(Boolean) : []);
  } catch (_) {
    return new Set();
  }
}

function rememberDismissedRun(runId) {
  const ids = Array.from(readDismissedRunIds()).filter((id) => id !== Number(runId));
  ids.push(Number(runId));
  window.localStorage?.setItem(DISMISSED_RUN_KEY, JSON.stringify(ids.slice(-50)));
}

function writeActiveRuns(rows) {
  const now = Date.now();
  const dismissed = readDismissedRunIds();
  const cleanRows = (rows || []).filter((row) => {
    const runId = Number(row.run_id || row.id);
    if (runId <= 0 || dismissed.has(runId)) return false;
    if (row.dismissed_at) return false;
    if (ACTIVE_RUN_TERMINAL_STATUSES.has(row.status || "") && row.finished_at) {
      return now - Date.parse(row.finished_at) < ACTIVE_RUN_RECENT_TTL_MS;
    }
    return true;
  });
  if (!cleanRows.length) {
    window.localStorage?.removeItem(ACTIVE_RUN_KEY);
    return;
  }
  const compactRows = cleanRows.slice(-6).map((row) => ({
    run_id: Number(row.run_id || row.id),
    task_type: row.task_type || "full_career_flow",
    label: row.label || taskLabel(row.task_type),
    package_id: row.package_id || row.run_id || row.id,
    created_at: row.created_at || null,
    status: row.status || "unknown",
    finished_at: row.finished_at || null,
    error: row.error || null,
  }));
  window.localStorage?.setItem(ACTIVE_RUN_KEY, JSON.stringify(compactRows));
}

function trackActiveRun(record) {
  const runId = Number(record?.run_id || record?.id || 0);
  if (!runId) return;
  const rows = readActiveRuns().filter((row) => Number(row.run_id || row.id) !== runId);
  rows.push({
    run_id: runId,
    task_type: record.task_type || record.run?.task_type || "full_career_flow",
    label: record.label || taskLabel(record.task_type || record.run?.task_type),
    package_id: record.package_id || runId,
    created_at: record.created_at || new Date().toISOString(),
    status: record.status || "queued",
    finished_at: record.finished_at || null,
    error: record.error || null,
  });
  writeActiveRuns(rows);
  void restoreActiveRuns();
}

function updateTrackedRun(run) {
  const runId = Number(run?.id || run?.run_id || 0);
  if (!runId) return;
  const rows = readActiveRuns();
  const existing = rows.find((row) => Number(row.run_id || row.id) === runId) || {};
  const status = run.status || existing.status || "unknown";
  const merged = {
    ...existing,
    run_id: runId,
    task_type: run.task_type || existing.task_type,
    label: existing.label || taskLabel(run.task_type),
    package_id: existing.package_id || userPackageId(run) || runId,
    created_at: existing.created_at || run.created_at || new Date().toISOString(),
    status,
    finished_at: ACTIVE_RUN_TERMINAL_STATUSES.has(status) ? (existing.finished_at || new Date().toISOString()) : null,
    error: run.error_message || run.output_json?.error || existing.error || null,
  };
  writeActiveRuns([...rows.filter((row) => Number(row.run_id || row.id) !== runId), merged]);
  void restoreActiveRuns();
}

function dismissActiveRun(runId) {
  rememberDismissedRun(runId);
  writeActiveRuns(readActiveRuns().filter((row) => Number(row.run_id || row.id) !== Number(runId)));
  void restoreActiveRuns();
}

function activeRunStatusLabel(status) {
  const labels = {
    queued: "排队中",
    running: "运行中",
    waiting_for_confirmation: "等待确认",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    canceled: "已取消",
    unknown: "状态待同步",
  };
  return labels[status] || status || "状态待同步";
}

function renderActiveRunMonitor(rows) {
  const el = $("#active-run-monitor");
  if (!el) return;
  if (!rows.length) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  const primary = rows[0] || {};
  const hasRunning = rows.some((row) => !ACTIVE_RUN_TERMINAL_STATUSES.has(row.status || ""));
  const hasFailed = rows.some((row) => row.status === "failed");
  const hasWaiting = rows.some((row) => row.status === "waiting_for_confirmation");
  const statusPriority = {
    waiting_for_confirmation: 0,
    running: 1,
    queued: 2,
    failed: 3,
    completed: 4,
    cancelled: 5,
    canceled: 5,
  };
  const orderedRows = [...rows].sort((left, right) => {
    const statusDelta = (statusPriority[left.status] ?? 9) - (statusPriority[right.status] ?? 9);
    if (statusDelta !== 0) return statusDelta;
    return Number(right.run_id || 0) - Number(left.run_id || 0);
  });
  const highlighted = orderedRows[0] || primary;
  const collapsed = window.localStorage?.getItem(ACTIVE_RUN_COLLAPSED_KEY) === "1";
  el.classList.toggle("collapsed", collapsed);
  el.innerHTML = `
    <div class="active-run-title">
      <span>${hasRunning ? "正在处理的求职流程" : "最近的求职流程"}</span>
      <span class="active-run-title-actions">
        <span class="status-pill ${hasWaiting || hasFailed ? "risk" : "ok"}">${escapeHtml(activeRunStatusLabel(highlighted.status))}</span>
        <button class="icon-button" type="button" title="${collapsed ? "展开流程状态" : "收起流程状态"}" data-toggle-active-runs>
          <i data-lucide="${collapsed ? "chevron-up" : "chevron-down"}"></i>
        </button>
      </span>
    </div>
    ${[highlighted].map((row) => `
      <div class="active-run-row">
        <div>
          <strong>${escapeHtml(row.label || taskLabel(row.task_type))} · ${escapeHtml(packageLabel(row.package_id || row.run_id))}</strong>
          <button class="icon-button" type="button" title="不再显示这个流程" data-dismiss-active-run="${escapeHtml(row.run_id)}"><i data-lucide="x"></i></button>
        </div>
        <small>${escapeHtml(activeRunStatusLabel(row.status))}${row.error ? ` · ${escapeHtml(row.error)}` : ""}</small>
      </div>
    `).join("")}
    ${orderedRows.length > 1 ? `<a class="active-run-more" href="/ui/agent-runs">另有 ${orderedRows.length - 1} 个流程，统一到历史记录查看</a>` : ""}
    <div class="flow-result-actions">
      <a class="button ghost" href="/ui/agent-runs"><i data-lucide="history"></i> 查看历史记录</a>
      ${rows.some((row) => row.status === "waiting_for_confirmation") ? `<a class="button primary" href="/ui/agent-runs"><i data-lucide="check-circle-2"></i> 去确认</a>` : ""}
    </div>
  `;
  if (window.lucide) window.lucide.createIcons();
}

function stageFromWorkflowName(name) {
  const map = {
    parse_user_request: "profile",
    plan_task: "profile",
    load_profile: "profile",
    search_jobs: "search",
    match_jobs: "match",
    select_job: "match",
    match_job: "match",
    tailor_resume: "tailor",
    tailor_resume_with_rag: "tailor",
    create_missing_tailored_resume: "tailor",
    fit_gate: "apply",
    ensure_resume_version: "apply",
    create_application_packet: "apply",
    generate_interview_prep: "interview",
  };
  return map[name] || "";
}

function updateCareerFlowFromStep(row) {
  const stage = stageFromWorkflowName(row.step_name || row.node_name || "");
  if (!stage) return;
  const status = row.status === "completed"
    ? "done"
    : row.status === "failed"
      ? "failed"
      : ["running", "started", "queued"].includes(row.status)
        ? "running"
        : "";
  if (!status) return;
  const detail = status === "done"
    ? "已完成"
    : status === "failed"
      ? (row.error_message || "失败")
      : stepLabel(row.step_name || row.node_name);
  setCareerStage(stage, status, detail);
}

async function restoreCareerFlowFromRun(run) {
  if (!$("#career-flow-steps") || !run?.id) return;
  const statusText = activeRunStatusLabel(run.status);
  renderCareerFlowMessage(
    run.status === "failed" ? "error" : "info",
    `${packageLabel(run.id)} ${statusText}。刷新或切换页面不会丢失进度，可在历史记录中查看完整 trace。`
  );
  try {
    const steps = await api(`/agent/runs/${run.id}/steps`);
    if (steps.length) {
      steps.forEach(updateCareerFlowFromStep);
    } else if (!ACTIVE_RUN_TERMINAL_STATUSES.has(run.status)) {
      setCareerStage("match", "running", `${statusText} #${run.id}`);
    }
  } catch (_) {
    if (!ACTIVE_RUN_TERMINAL_STATUSES.has(run.status)) {
      setCareerStage("match", "running", `${statusText} #${run.id}`);
    }
  }
}

async function restoreActiveRuns() {
  let stored = readActiveRuns();
  if (!stored.length) {
    stored = await recentRunsFromServer();
  }
  if (!stored.length) {
    renderActiveRunMonitor([]);
    if (activeRunMonitorTimer) clearTimeout(activeRunMonitorTimer);
    activeRunMonitorTimer = null;
    return;
  }
  const results = await Promise.all(stored.map(async (record) => {
    const runId = Number(record.run_id || record.id);
    try {
      const run = await api(`/agent/runs/${runId}`);
      return { record, run };
    } catch (error) {
      return { record, error };
    }
  }));
  const keep = [];
  const visible = [];
  for (const item of results) {
    const runId = Number(item.record.run_id || item.record.id);
    if (item.run) {
      const status = item.run.status || "unknown";
      const finishedAt = ACTIVE_RUN_TERMINAL_STATUSES.has(status)
        ? (item.record.finished_at || item.run.updated_at || item.run.completed_at || new Date().toISOString())
        : null;
      const merged = {
        ...item.record,
        run_id: runId,
        package_id: item.record.package_id || runId,
        task_type: item.run.task_type || item.record.task_type,
        label: item.record.label || taskLabel(item.run.task_type),
        status,
        finished_at: finishedAt,
        error: item.run.error_message || item.run.output_json?.error || item.record.error || null,
        run: item.run,
      };
      const recentTerminal = ACTIVE_RUN_TERMINAL_STATUSES.has(status)
        && finishedAt
        && Date.now() - Date.parse(finishedAt) < ACTIVE_RUN_RECENT_TTL_MS;
      if (!ACTIVE_RUN_TERMINAL_STATUSES.has(status) || recentTerminal) {
        keep.push(merged);
        visible.push(merged);
      }
    } else {
      const merged = {
        ...item.record,
        run_id: runId,
        status: "unknown",
        error: item.error?.message || "暂时无法同步",
      };
      keep.push(merged);
      visible.push(merged);
    }
  }
  writeActiveRuns(keep);
  renderActiveRunMonitor(visible);
  if (activeRunMonitorTimer) clearTimeout(activeRunMonitorTimer);
  activeRunMonitorTimer = keep.some((row) => !ACTIVE_RUN_TERMINAL_STATUSES.has(row.status || "")) ? setTimeout(() => {
    void restoreActiveRuns();
  }, 4000) : null;
}

async function recentRunsFromServer() {
  try {
    const dismissed = readDismissedRunIds();
    const rows = await api("/agent/runs");
    const now = Date.now();
    return (rows || [])
      .filter((run) => {
        const runId = Number(run.id);
        if (!runId || dismissed.has(runId)) return false;
        if (!ACTIVE_RUN_TERMINAL_STATUSES.has(run.status || "")) return true;
        const timeText = run.updated_at || run.created_at;
        return timeText && now - Date.parse(timeText) < ACTIVE_RUN_RECENT_TTL_MS;
      })
      .slice(0, 3)
      .map((run) => ({
        run_id: run.id,
        task_type: run.task_type,
        label: taskLabel(run.task_type),
        package_id: userPackageId(run),
        created_at: run.created_at,
        status: run.status,
        finished_at: ACTIVE_RUN_TERMINAL_STATUSES.has(run.status || "") ? (run.updated_at || run.created_at || new Date().toISOString()) : null,
        error: run.error_message || run.output_json?.error || null,
      }));
  } catch (_) {
    return [];
  }
}

function setCareerStage(stage, status, detail = "") {
  const item = document.querySelector(`#career-flow-steps [data-stage="${stage}"]`);
  if (!item) return;
  item.classList.remove("running", "done", "failed");
  if (status) item.classList.add(status);
  const small = item.querySelector("small");
  if (small) small.textContent = detail || (status === "done" ? "已完成" : status === "running" ? "运行中" : "等待开始");
}

function resetCareerFlow() {
  CAREER_FLOW_STAGES.forEach((stage) => setCareerStage(stage, "", "等待开始"));
  const result = $("#career-flow-result");
  if (result) result.innerHTML = "";
}

function renderCareerFlowMessage(kind, message) {
  const result = $("#career-flow-result");
  if (!result) return;
  result.innerHTML = `<article class="item ${kind === "error" ? "validation-risk" : ""}">${escapeHtml(message)}</article>`;
}

function careerFlowRunLink(run, label) {
  if (!run?.id) return "";
  return `<a class="button ghost" href="/ui/agent-runs"><i data-lucide="route"></i> ${escapeHtml(label)} #${run.id}</a>`;
}

function renderCareerFlowResult(state) {
  const result = $("#career-flow-result");
  if (!result) return;
  const selected = state.selectedJob || {};
  const tailor = state.tailorRun?.output_json || {};
  const apply = state.applyRun?.output_json || {};
  const interview = state.interviewRun?.output_json || {};
  const packageId = state.packageId || state.fullRun?.id || state.runId || "";
  result.innerHTML = `
    <article class="item flow-result-card">
      <div class="item-title">
        <span>${escapeHtml(packageLabel(packageId))}</span>
        <span class="status-pill ok">${escapeHtml(selected.overall_score ?? "ready")}</span>
      </div>
      <div class="meta">${escapeHtml(selected.title || "已完成")} · ${escapeHtml(selected.company || "")} · Profile ${escapeHtml(state.profile?.id || "-")} · Job ${escapeHtml(selected.job_id || "-")}</div>
      ${tags(selected.matched_skills || [])}
      <div class="embedded-business-summary">
        ${businessSummaryHtml(state.fullRun?.output_json?.business_summary, true)}
      </div>
      <div class="flow-result-actions">
        ${tailor.resume_version_id ? packageAction("/ui/resumes", "file-check-2", "定制简历", packageId) : ""}
        ${apply.application_id ? packageAction("/ui/applications", "send", "投递材料", packageId) : ""}
        ${interview.interview_prep_id ? packageAction(`/ui/prep?job_id=${escapeHtml(selected.job_id || "")}`, "messages-square", "面试准备", packageId) : ""}
        ${state.fullRun?.id ? packageAction("/ui/agent-runs", "route", "查看流程", packageId) : ""}
      </div>
    </article>
  `;
  if (window.lucide) window.lucide.createIcons();
}

function guidedProfilePayload(raw, enabledSections = null, form = null) {
  const enabled = enabledSections ? new Set(enabledSections) : null;
  const skills = splitList(raw.skills);
  const targetRoles = resumeSectionEnabled(enabled, "intent") ? splitList(raw.target_roles) : [];
  const projectDescription = raw.project_description || raw.project || "";
  const projectLines = projectDescription.split("\n").filter(Boolean);
  const projectTechStack = splitList(raw.project_tech_stack).length ? splitList(raw.project_tech_stack) : skills.slice(0, 8);
  const projects = resumeSectionEnabled(enabled, "projects")
    ? (form
      ? collectRepeatList(form, "projects", (entry) => ({
        name: valueIn(entry, "project_name") || "项目经历",
        description: valueIn(entry, "project_description"),
        tech_stack: splitList(valueIn(entry, "project_tech_stack")),
        impact: valueIn(entry, "project_impact"),
      }))
      : hasAnyValue(raw.project_name, projectDescription, raw.project_impact, raw.project_tech_stack) ? [{
      name: raw.project_name || projectLines[0]?.split("：")[0] || projectLines[0] || "项目经历",
      description: projectDescription,
      tech_stack: projectTechStack,
      impact: raw.project_impact || projectLines.at(-1) || "",
    }] : [])
    : [];
  const education = resumeSectionEnabled(enabled, "education")
    ? (form
      ? collectRepeatList(form, "education", (entry) => ({
        school: valueIn(entry, "education_school"),
        degree: valueIn(entry, "education_degree"),
        major: valueIn(entry, "education_major"),
        duration: valueIn(entry, "education_duration"),
        details: valueIn(entry, "education_details"),
      }))
      : hasAnyValue(raw.education_school, raw.education_degree, raw.education_major, raw.education_duration, raw.education_details) ? [{
      school: raw.education_school || "",
      degree: raw.education_degree || "",
      major: raw.education_major || "",
      duration: raw.education_duration || "",
      details: raw.education_details || "",
    }] : [])
    : [];
  const workExperience = resumeSectionEnabled(enabled, "work")
    ? (form
      ? collectRepeatList(form, "work", (entry) => ({
        company: valueIn(entry, "work_company"),
        role: valueIn(entry, "work_role"),
        duration: valueIn(entry, "work_duration"),
        details: valueIn(entry, "work_details"),
        tech_stack: splitList(valueIn(entry, "work_tech_stack")),
      }))
      : hasAnyValue(raw.work_company, raw.work_role, raw.work_duration, raw.work_details, raw.work_tech_stack) ? [{
      company: raw.work_company || "",
      role: raw.work_role || "",
      duration: raw.work_duration || "",
      details: raw.work_details || "",
      tech_stack: splitList(raw.work_tech_stack),
    }] : [])
    : [];
  const campusExperience = resumeSectionEnabled(enabled, "campus")
    ? (form
      ? collectRepeatList(form, "campus", (entry) => ({
        company: valueIn(entry, "campus_organization"),
        role: valueIn(entry, "campus_role"),
        duration: valueIn(entry, "campus_duration"),
        details: valueIn(entry, "campus_details"),
        tech_stack: [],
      }))
      : hasAnyValue(raw.campus_organization, raw.campus_role, raw.campus_duration, raw.campus_details) ? [{
      company: raw.campus_organization || "",
      role: raw.campus_role || "",
      duration: raw.campus_duration || "",
      details: raw.campus_details || "",
      tech_stack: [],
    }] : [])
    : [];
  return {
    name: raw.name,
    email: raw.email,
    phone: raw.phone,
    location: raw.location,
    availability: resumeSectionEnabled(enabled, "intent") ? raw.availability : undefined,
    headline: resumeSectionEnabled(enabled, "intent") ? raw.headline : undefined,
    self_summary: resumeSectionEnabled(enabled, "summary") ? raw.self_summary : undefined,
    enabled_sections: enabledSections || [],
    target_roles: targetRoles,
    education,
    skills: resumeSectionEnabled(enabled, "skills") ? skills : [],
    projects,
    work_experience: workExperience,
    campus_experience: campusExperience,
    certifications: resumeSectionEnabled(enabled, "extras") ? splitList(raw.certifications) : [],
    awards: resumeSectionEnabled(enabled, "extras") ? splitList(raw.awards) : [],
    languages: resumeSectionEnabled(enabled, "extras") ? splitList(raw.languages) : [],
    portfolio_links: resumeSectionEnabled(enabled, "portfolio") ? splitList(raw.portfolio_links) : [],
  };
}

async function createProfileForCareerFlow(form, raw) {
  const existingId = Number(raw.profile_id || 0);
  if (existingId > 0) {
    return await api(`/profiles/${existingId}`);
  }
  const file = form.elements.resume_file?.files?.[0];
  if (file) {
    return await parseResumeFileIntoStartForm(form, file);
  }
  throw new Error("请先选择已有简历档案，或上传 PDF 自动建立档案；没有简历时请到“简历建档”页面手动填写或用自然语言生成。");
}

async function uploadProfileFile(file) {
  const data = new FormData();
  data.append("file", file);
  const response = await fetch("/profiles/upload", { method: "POST", body: data, headers: authHeaders() });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function selectedStartActions(form) {
  return ["search_jobs"];
}

function optionalStartActions(form) {
  return Array.from(form?.querySelectorAll('input[name="selected_actions"]:checked') || []).map((input) => input.value);
}

function startActionLabel(action) {
  const labels = {
    create_profile: "简历档案",
    search_jobs: "岗位搜索",
    tailor_resume: "定制简历",
    quick_apply: "投递材料",
    interview_prep: "面试准备",
  };
  return labels[action] || action;
}

function profileContextFromStartForm(raw) {
  return {};
}

function hasProfileContext(context) {
  return Object.entries(context || {}).some(([key, value]) => {
    if (key === "projects") return Array.isArray(value) && value.some((item) => hasAnyValue(item.name, item.description));
    if (Array.isArray(value)) return value.length > 0;
    return hasAnyValue(value);
  });
}

function buildNaturalInstruction(raw, actions) {
  const text = String(raw.instruction || "").trim();
  if (text) return text;
  if (actions.length) {
    return `请根据我提供的简历和岗位信息完成这些内容：${actions.map(startActionLabel).join("、")}。`;
  }
  return "请根据我提供的简历和岗位信息，完成适合当前求职目标的求职流程。";
}

function projectTextFromProfile(structured) {
  const projects = structured?.projects || [];
  return projects.map((project) => {
    const tech = (project.tech_stack || []).length ? `\n技术栈：${project.tech_stack.join("、")}` : "";
    const impact = project.impact ? `\n结果：${project.impact}` : "";
    return `${project.name || "项目"}：${project.description || ""}${tech}${impact}`.trim();
  }).filter(Boolean).join("\n\n");
}

function populateStartFormFromProfile(form, profile, source = "existing") {
  if (!form || !profile) return;
  const structured = profile.structured_profile_json || {};
  if (form.profile_id) form.profile_id.value = profile.id || "";
  if (form.location && structured.location) form.location.value = structured.location;
  if (form.query && !form.query.value.trim()) {
    const roles = profile.target_roles_json || structured.target_roles || [];
    form.query.value = roles[0] || "Agent 开发实习生";
  }
  updateSelectedProfileCard(profile, source);
}

function setPdfParseStatus(form, message, kind = "") {
  const status = form?.querySelector("#pdf-parse-status");
  if (!status) return;
  status.textContent = message;
  status.className = `field-hint ${kind}`.trim();
}

async function parseResumeFileIntoStartForm(form, file) {
  if (!file) return null;
  const signature = `${file.name}:${file.size}:${file.lastModified}`;
  if (form.dataset.parsedResumeSignature === signature && form.dataset.parsedProfileId) {
    return api(`/profiles/${form.dataset.parsedProfileId}`);
  }
  setPdfParseStatus(form, "正在解析 PDF，并建立简历档案...", "running");
  const profile = await uploadProfileFile(file);
  form.dataset.parsedResumeSignature = signature;
  form.dataset.parsedProfileId = String(profile.id || "");
  profilePickerRows = [profile, ...profilePickerRows.filter((item) => Number(item.id) !== Number(profile.id))];
  populateStartFormFromProfile(form, profile, "pdf");
  updateStartInputGuidance(form);
  setPdfParseStatus(form, `PDF 已解析为 Profile #${profile.id}，后续流程会使用该档案。`, "ok");
  toast(`PDF 已解析为 Profile #${profile.id}`);
  return profile;
}

function updateStartInputGuidance(form) {
  const card = $("#input-guidance");
  if (!card || !form) return;
  const raw = formJson(form);
  const hasPreference = Boolean(String(raw.instruction || "").trim());
  const hasProfile = Boolean(raw.profile_id || form.elements.resume_file?.files?.[0]);
  let title = "简历不是搜索岗位的前置条件";
  let detail = "不提供简历时按求职需求检索；提供简历后还会为每个岗位计算匹配项和能力缺口。";
  let buttonText = "浏览 Agent 岗位";
  let submitSummary = "将浏览系统中的 Agent 岗位";
  if (hasPreference && hasProfile) {
    title = "将同时使用求职需求和简历";
    detail = "明确填写的城市和岗位偏好优先，简历中的技能和经历用于补充检索与匹配证据。";
    buttonText = "搜索并匹配岗位";
    submitSummary = "将结合求职需求和简历匹配岗位";
  } else if (hasProfile) {
    title = "将从简历中推断适合的岗位";
    detail = "系统会读取目标岗位、技能和经历生成搜索条件，结果页仍可修改需求后重新搜索。";
    buttonText = "解析简历并匹配岗位";
    submitSummary = "将从简历推断方向并匹配岗位";
  } else if (hasPreference) {
    title = "将按你的求职需求搜索";
    detail = "不需要先建立简历。看到感兴趣的岗位后，再选择或上传简历进行差距分析。";
    buttonText = "搜索岗位";
    submitSummary = "将按求职需求搜索岗位";
  }
  card.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span>`;
  const buttonLabel = $("#career-start-submit span");
  if (buttonLabel) buttonLabel.textContent = buttonText;
  const summary = $("#start-submit-summary");
  if (summary) summary.textContent = submitSummary;
}

function topMatchedJob(run) {
  const matches = run.output_json?.matches || [];
  if (!matches.length) {
    const sourceErrors = run.output_json?.source_errors || {};
    const sourceNames = Object.keys(sourceErrors);
    const suffix = sourceNames.length ? `岗位源暂时不可用：${sourceNames.join("、")}。可在控制台查看详细错误。` : "可以换一个关键词或粘贴目标 JD 后重试。";
    throw new Error(`没有找到可匹配岗位。${suffix}`);
  }
  return matches[0];
}

async function resumeAgentRun(runId, payload) {
  return api(`/agent/runs/${runId}/resume`, { method: "POST", body: JSON.stringify(payload) });
}

async function createAgentRun(payload, label, options = {}) {
  const run = await api("/agent/runs", { method: "POST", body: JSON.stringify(payload) });
  if (run.id) {
    trackActiveRun({
      run_id: run.id,
      task_type: payload.task_type,
      label,
      package_id: userPackageId(run),
      created_at: run.created_at,
      status: run.status,
      error: run.error_message || run.output_json?.error || null,
    });
    updateTrackedRun(run);
  }
  if (run.status === "waiting_for_confirmation" && options.autoConfirmApplication) {
    toast("投递包生成前需要确认，已按一键流程继续。");
    const resumed = await resumeAgentRun(run.id, {
      confirmed: true,
      note: options.confirmationNote || "用户在一键流程中确认生成投递包。",
      resume_json: { source: "frontend_auto_confirm" },
    });
    updateTrackedRun(resumed);
    if (resumed.status !== "completed") {
      const message = resumed.error_message || resumed.output_json?.error || JSON.stringify(resumed.output_json || {});
      throw new Error(`${label}确认后失败：${message}`);
    }
    return resumed;
  }
  if (run.status !== "completed") {
    const message = run.error_message || run.output_json?.error || JSON.stringify(run.output_json || {});
    throw new Error(`${label}失败：${message}`);
  }
  return run;
}

async function createBackgroundAgentRun(payload) {
  const run = await api("/agent/runs/background", { method: "POST", body: JSON.stringify(payload) });
  trackActiveRun({
    run_id: run.id,
    task_type: payload.task_type,
    label: taskLabel(payload.task_type),
    package_id: run.id,
    created_at: run.created_at,
    status: run.status,
    error: run.error_message || run.output_json?.error || null,
  });
  updateTrackedRun(run);
  return run;
}

function updateCareerFlowFromEvent(event) {
  const node = event.node_name || event.event_json?.node_name || "";
  const type = event.event_type || "";
  const running = type.includes("started") || type.includes("update");
  const done = type.includes("completed");
  const status = running ? "running" : done ? "done" : "";
  if (!status) return;
  const stage = stageFromWorkflowName(node);
  if (!stage) return;
  const detail = status === "running" ? eventLabel(type, node) : "已完成";
  setCareerStage(stage, status, detail);
}

function waitForAgentRun(runId, options = {}) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = async () => {
      if (settled) return;
      settled = true;
      try {
        let run = await api(`/agent/runs/${runId}`);
        if (run.status === "waiting_for_confirmation" && options.autoConfirmApplication) {
          toast("投递包生成前需要确认，已按一键流程继续。");
          run = await resumeAgentRun(run.id, {
            confirmed: true,
            note: options.confirmationNote || "用户在一键流程中确认生成投递包。",
            resume_json: { source: "frontend_auto_confirm" },
          });
        }
        if (run.status === "completed") {
          updateTrackedRun(run);
          resolve(run);
        } else {
          if (run.status === "waiting_for_confirmation") {
            trackActiveRun({
              run_id: run.id,
              task_type: run.task_type,
              label: taskLabel(run.task_type),
              package_id: run.id,
              created_at: run.created_at,
            });
          } else if (run.status === "failed") {
            updateTrackedRun(run);
          }
          reject(new Error(run.error_message || run.output_json?.error || `流程状态：${run.status}`));
        }
      } catch (error) {
        reject(error);
      }
    };
    const fallback = setInterval(async () => {
      try {
        const run = await api(`/agent/runs/${runId}`);
        if (["completed", "failed", "waiting_for_confirmation"].includes(run.status)) {
          clearInterval(fallback);
          finish();
        }
      } catch (_) {
        // SSE path will surface the error if the stream is still alive.
      }
    }, 1500);
    subscribeAgentRunEvents(runId, {
      onEvent: (event) => {
        updateCareerFlowFromEvent(event);
        options.onEvent?.(event);
      },
      onTerminal: async (_payload, type) => {
        if (type === "graph_interrupt" && options.autoConfirmApplication) return;
        clearInterval(fallback);
        await finish();
      },
      onError: () => {},
    });
  });
}

function selectedJobFromMatch(job, match) {
  return {
    job_id: job.id,
    title: job.title,
    company: job.company,
    overall_score: match.overall_score,
    matched_skills: match.matched_skills_json || [],
    missing_skills: match.missing_skills_json || [],
    apply_url: job.apply_url,
  };
}

async function resolveDirectJobForCareerFlow(raw, profileId) {
  const existingJobId = Number(raw.job_id || 0);
  let job = null;
  if (existingJobId > 0) {
    job = await api(`/jobs/${existingJobId}`);
  } else if ((raw.jd_text || "").trim().length >= 20) {
    job = await api("/jobs", {
      method: "POST",
      body: JSON.stringify({
        title: raw.query || "目标岗位",
        company: raw.company || null,
        location: raw.location || null,
        jd_text: raw.jd_text,
      }),
    });
  }
  if (!job) return null;
  const match = await api("/matches", {
    method: "POST",
    body: JSON.stringify({ profile_id: Number(profileId), job_id: Number(job.id) }),
  });
  return selectedJobFromMatch(job, match);
}

async function runCareerStartFlow(form) {
  resetCareerFlow();
  const submitButton = form.querySelector("button[type='submit']");
  if (submitButton) submitButton.disabled = true;
  try {
    const raw = formJson(form);
    let profileId = Number(raw.profile_id || 0) || null;
    const file = form.elements.resume_file?.files?.[0];
    setCareerStage("profile", "running", profileId || file ? "准备简历信息" : "不使用简历");
    if (!profileId && file) {
      const profile = await parseResumeFileIntoStartForm(form, file);
      profileId = Number(profile?.id || 0) || null;
    }
    setCareerStage("profile", "done", profileId ? `简历 #${profileId}` : "按需求搜索");
    setCareerStage("search", "running", raw.source_mode === "corpus" ? "检索岗位库" : "检索真实来源和岗位库");
    if (profileId) setCareerStage("match", "running", "准备简历匹配");
    const body = await api("/job-discovery/sessions", {
      method: "POST",
      body: JSON.stringify({
        preference_text: String(raw.instruction || "").trim() || null,
        profile_id: profileId,
        location: raw.location || null,
        internship_only: Boolean(form.internship_only?.checked),
        limit: Number(raw.limit || 20),
        source_mode: raw.source_mode || "hybrid",
      }),
    });
    window.localStorage?.setItem(JOB_DISCOVERY_SESSION_KEY, String(body.session.id));
    setCareerStage("search", "done", `${body.results.length} 个岗位`);
    setCareerStage("match", "done", profileId ? "已完成简历匹配" : "岗位详情中可补充简历");
    setCareerStage("results", "done", `搜索记录 #${body.session.id}`);
    renderCareerFlowMessage("success", `已找到 ${body.results.length} 个岗位，正在打开搜索结果。`);
    window.location.assign(`/ui/jobs?session_id=${body.session.id}`);
  } catch (error) {
    const current = CAREER_FLOW_STAGES.find((stage) => document.querySelector(`#career-flow-steps [data-stage="${stage}"]`)?.classList.contains("running"));
    if (current) setCareerStage(current, "failed", "失败");
    renderCareerFlowMessage("error", error.message);
    toast(error.message);
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
}

function fillCareerDemo(form, scenario = "tailor") {
  if (!form) return;
  const scenarios = {
    match: {
      instruction: "想找深圳、广州或远程的 Agent 开发实习，偏 RAG、工作流、工具调用和后端工程，排除纯产品和纯运营岗位。",
    },
    backend: {
      instruction: "想找北京或杭州的 LLM 应用后端实习，关注 FastAPI、Redis、异步并发、Agent 平台和可观测性。",
    },
    rag: {
      instruction: "想找中文场景的 RAG 或 Agent 检索工程实习，偏向量检索、混合召回、reranker、评测和 prompt injection 防护。",
    },
  };
  const selectedScenario = scenarios[scenario] || scenarios.match;
  if (form.instruction) {
    form.instruction.value = selectedScenario.instruction;
  }
  if (form.limit) form.limit.value = "20";
  updateStartInputGuidance(form);
  toast("已填入求职需求示例");
}

function updateCareerFlowFromNaturalResult(body) {
  const data = body.result_json || {};
  const failed = body.status === "failed";
  if (data.profile?.id) setCareerStage("profile", failed ? "failed" : "done", failed ? "失败" : packageLabel(body.run_id));
  if (data.job?.id || data.matches?.length) setCareerStage("search", failed ? "failed" : "done", failed ? "失败" : packageLabel(body.run_id));
  if (data.job?.id || data.matches?.length || data.tailor || data.application || data.interview_prep) {
    setCareerStage("match", failed ? "failed" : "done", failed ? "失败" : packageLabel(body.run_id));
  }
  if (data.tailor?.resume_version_id) setCareerStage("tailor", failed ? "failed" : "done", failed ? "失败" : packageLabel(body.run_id));
  if (data.application?.application_id || data.requires_confirmation) setCareerStage("apply", failed ? "failed" : "done", failed ? "失败" : packageLabel(body.run_id));
  if (data.interview_prep?.interview_prep_id) setCareerStage("interview", failed ? "failed" : "done", failed ? "失败" : packageLabel(body.run_id));
}

function renderNaturalLanguageResult(body) {
  const result = $("#career-flow-result") || $("#natural-language-result");
  if (!result) return;
  const data = body.result_json || {};
  const runs = data.agent_runs || [];
  const failed = body.status === "failed";
  const packageId = body.run_id;
  const links = [];
  const seenLinks = new Set();
  if (body.run_id) pushUniqueAction(links, seenLinks, "agent-runs", packageAction("/ui/agent-runs", "history", "查看历史记录", packageId));
  if (data.profile?.id) pushUniqueAction(links, seenLinks, "profiles", packageAction("/ui/profiles", "file-user", "简历档案", packageId));
  if (data.job?.id) pushUniqueAction(links, seenLinks, "jobs", packageAction("/ui/jobs", "briefcase-business", "目标岗位", packageId));
  if (data.tailor?.resume_version_id) pushUniqueAction(links, seenLinks, "resumes", packageAction("/ui/resumes", "file-check-2", "定制简历", packageId));
  if (data.application?.application_id) pushUniqueAction(links, seenLinks, "applications", packageAction("/ui/applications", "send", "投递材料", packageId));
  if (data.interview_prep?.interview_prep_id) pushUniqueAction(links, seenLinks, "prep", packageAction("/ui/prep", "messages-square", "面试准备", packageId));
  if (data.matches?.length) {
    pushUniqueAction(
      links,
      seenLinks,
      "matched-jobs",
      `<a class="button ghost" href="/ui/jobs"><i data-lucide="search"></i> 推荐岗位 ${data.matches.length} 个 · ${escapeHtml(packageLabel(packageId))}</a>`
    );
  }
  const runActionKeys = {
    find_jobs_for_profile: "jobs",
    tailor_resume_for_job: "resumes",
    quick_apply: "applications",
    prepare_interview_for_job: "prep",
    full_career_flow: "agent-runs",
    natural_language_request: "agent-runs",
  };
  runs.forEach((run) => pushUniqueAction(
    links,
    seenLinks,
    runActionKeys[run.task_type] || run.task_type || "agent-runs",
    `<a class="button ghost" href="/ui/agent-runs"><i data-lucide="route"></i> ${escapeHtml(taskLabel(run.task_type))} · ${escapeHtml(packageLabel(packageId))}</a>`
  ));
  result.innerHTML = `
    <article class="item flow-result-card">
      <div class="item-title">
        <span>${escapeHtml(packageLabel(packageId))}</span>
        <span class="status-pill ${failed ? "risk" : "ok"}">${failed ? "需处理" : "已完成"}</span>
      </div>
      <div class="meta">${escapeHtml(naturalResultSummary(body))}${body.repair_attempts?.length ? ` · 自动修复 ${body.repair_attempts.length} 次` : ""}</div>
      <div class="flow-result-actions">${links.join("")}</div>
    </article>
  `;
  updateCareerFlowFromNaturalResult(body);
  if (window.lucide) window.lucide.createIcons();
}

function naturalResultSummary(body) {
  const data = body.result_json || {};
  if (body.status === "failed") return body.user_message || "处理失败";
  const parts = [];
  if (data.profile?.id) parts.push("简历档案");
  if (data.job?.id) parts.push("目标岗位");
  if (data.matches?.length) parts.push(`${data.matches.length} 个推荐岗位`);
  if (data.tailor?.resume_version_id) parts.push("定制简历");
  if (data.application?.application_id) parts.push("投递材料");
  if (data.interview_prep?.interview_prep_id) parts.push("面试准备");
  if (data.requires_confirmation) parts.push("待确认事项");
  return parts.length ? `已生成：${parts.join("、")}` : "处理完成";
}

async function runNaturalLanguageRequest(form) {
  resetCareerFlow();
  setCareerStage("profile", "running", "理解需求");
  const result = $("#career-flow-result") || $("#natural-language-result");
  const submitButton = form.querySelector("button[type='submit']");
  if (submitButton) submitButton.disabled = true;
  if (result) result.innerHTML = `<article class="item meta">Agent 正在理解需求并执行，复杂任务可能需要几十秒...</article>`;
  try {
    const file = form.elements.resume_file?.files?.[0];
    let raw = formJson(form);
    let profileId = raw.profile_id ? Number(raw.profile_id) : null;
    if (file && !profileId) {
      const profile = await parseResumeFileIntoStartForm(form, file);
      profileId = profile.id;
      raw = formJson(form);
    }
    if (!profileId) {
      throw new Error("请先选择已有简历档案，或上传 PDF 自动建立档案；没有简历时请到“简历建档”页面创建。");
    }
    const actions = selectedStartActions(form);
    const body = await api("/assistant/natural-language", {
      method: "POST",
      body: JSON.stringify({
        instruction: buildNaturalInstruction(raw, actions),
        profile_id: profileId,
        job_id: raw.job_id ? Number(raw.job_id) : null,
        profile_context: null,
        selected_actions: actions,
        jd_text: raw.jd_text || null,
        location: raw.location || null,
        query: raw.query || "Agent 开发实习生",
        limit: raw.limit ? Number(raw.limit) : 8,
      }),
    });
    renderNaturalLanguageResult(body);
    toast("自然语言需求已处理完成");
  } catch (error) {
    if (error.body?.run_id) {
      renderNaturalLanguageResult(error.body);
    } else if (result) {
      result.innerHTML = `<article class="item validation-risk">${escapeHtml(error.message)}</article>`;
    }
    toast(error.message);
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
}

async function loadInterviewExperiences() {
  const rows = await api("/interview-prep/experiences");
  renderItems("#interview-experience-list", rows, (row) => {
    const credibility = row.credibility_json || {};
    const questions = row.extracted_questions_json || [];
    return `
      <article class="item">
        <div class="item-title">
          <span>#${row.id} ${escapeHtml(row.title || row.role_keyword || "同岗面经")}</span>
          <span class="status-pill ${questions.length ? "ok" : ""}">${questions.length} 道题</span>
        </div>
        <div class="meta">${escapeHtml(row.source_site)} · 岗位 ${row.job_id || "未绑定"} · 可信度 ${escapeHtml(credibility.score ?? "-")}</div>
        ${tags(row.topics_json || [])}
        <ul class="compact-list">${questions.slice(0, 4).map((item) => `<li>${escapeHtml(item.question)}</li>`).join("")}</ul>
      </article>
    `;
  });
}

function metricCell(label, value) {
  return `<div><strong>${escapeHtml(label)}</strong><div class="meta">${escapeHtml(value)}</div></div>`;
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function sourceSiteLabel(source) {
  const labels = {
    nowcoder: "牛客网",
    offershow: "OfferShow",
    xiaohongshu: "小红书",
  };
  return labels[source] || source || "";
}

function evaluationSummaryGrid(summary) {
  const type = summary.evaluation_type || "";
  if (type === "interview_source_smoke") {
    return `
      ${metricCell("可达源", percent(summary.reachable_source_rate))}
      ${metricCell("有结果源", percent(summary.result_source_rate))}
      ${metricCell("结果数", summary.total_result_count || 0)}
      ${metricCell("面经信号", percent(summary.interview_signal_rate))}
      ${metricCell("岗位相关", percent(summary.query_relevance_rate))}
      ${metricCell("可抽取", percent(summary.content_extractable_rate))}
    `;
  }
  if (type === "llm_workflow") {
    return `
      ${metricCell("完成率", percent(summary.completed_rate))}
      ${metricCell("端到端通过", percent(summary.end_to_end_pass_rate))}
      ${metricCell("JD 解析", percent(summary.jd_parse_success_rate))}
      ${metricCell("Fit 标签", percent(summary.fit_label_accuracy))}
      ${metricCell("简历定制", percent(summary.tailor_pass_rate))}
      ${metricCell("Guardrail", percent(summary.guardrail_pass_rate))}
    `;
  }
  return `
    ${metricCell("状态", summary.status || "-")}
    ${metricCell("样例数", summary.case_count ?? summary.total_result_count ?? "-")}
    ${metricCell("通过率", summary.pass_rate !== undefined ? percent(summary.pass_rate) : "-")}
  `;
}

function renderInterviewSourceSmoke(run) {
  const summary = run?.summary_json || {};
  const cases = run?.case_results_json || [];
  if (!run || summary.evaluation_type !== "interview_source_smoke") {
    return `<div class="item meta">暂无面经源探测结果</div>`;
  }
  return `
    <article class="item">
      <div class="item-title">
        <span>#${run.id} ${escapeHtml(summary.query || "面经源探测")}</span>
        <span class="status-pill ${summary.status === "completed" ? "ok" : ""}">${escapeHtml(summary.status || run.status)}</span>
      </div>
      <div class="meta">sources=${escapeHtml((summary.sources || []).join(", "))} / latency=${escapeHtml(summary.latency_ms || 0)}ms</div>
      <div class="validation-panel ${summary.status === "completed" ? "validation-ok" : "validation-risk"}">
        <div class="validation-grid">${evaluationSummaryGrid(summary)}</div>
      </div>
      ${summary.source_errors && Object.keys(summary.source_errors).length ? `<h3>源错误</h3><pre>${escapeHtml(JSON.stringify(summary.source_errors, null, 2))}</pre>` : ""}
      ${cases.map((row) => `
        <h3>${escapeHtml(row.source)} <span class="tag">${escapeHtml(row.status)}</span></h3>
        <div class="meta">可达=${escapeHtml(row.source_reachable)} / 结果=${escapeHtml(row.result_count || 0)} / 耗时=${escapeHtml(row.latency_ms || 0)}ms</div>
        <ul class="compact-list">${(row.sample_experiences || []).slice(0, 3).map((item) => `
          <li>
            <span class="tag">${item.interview_signal ? "面经" : "弱信号"}</span>
            <span class="tag">${item.query_relevant ? "相关" : "低相关"}</span>
            ${item.url ? `<a href="${escapeHtml(item.url)}" target="_blank">${escapeHtml(item.title)}</a>` : escapeHtml(item.title)}
            <button
              class="ghost"
              type="button"
              data-import-interview-candidate
              data-source="${escapeHtml(item.source || row.source || "")}"
              data-title="${escapeHtml(item.title || "")}"
              data-url="${escapeHtml(item.url || "")}"
              data-snippet="${escapeHtml(item.snippet_preview || "")}"
            ><i data-lucide="copy-plus"></i> 填入导入草稿</button>
            <div class="meta">${escapeHtml(item.snippet_preview || "")}</div>
          </li>
        `).join("")}</ul>
      `).join("")}
    </article>
  `;
}

function llmLogStats(logs) {
  const stats = {
    retry1: 0,
    retry2: 0,
    repair: 0,
    failed: 0,
  };
  (logs || []).forEach((row) => {
    if (row.trace_name === "jd_parser.parse_jd.retry_1") stats.retry1 += 1;
    if (row.trace_name === "jd_parser.parse_jd.retry_2") stats.retry2 += 1;
    if (row.trace_name === "jd_parser.parse_jd.repair_json") stats.repair += 1;
    if (row.status === "failed") stats.failed += 1;
  });
  return stats;
}

function renderStageDetails(stage) {
  const details = stage.details || {};
  if (details.error) return `<div class="meta">错误：${escapeHtml(details.error)}</div>`;
  const fields = [
    ["profile_skill_recall", "Profile skill recall"],
    ["jd_skill_recall", "JD skill recall"],
    ["evidence_hit_rate", "证据命中"],
    ["predicted_fit_label", "预测标签"],
    ["predicted_fit_score", "Fit 分"],
    ["risk_level", "风险"],
    ["hallucination_count", "幻觉数"],
    ["case_passed", "Case 通过"],
  ];
  const parts = fields
    .filter(([key]) => details[key] !== undefined && details[key] !== null)
    .map(([key, label]) => `${label}=${details[key]}`);
  const evidence = (details.top_evidence || []).slice(0, 2).map((item) => item.text_preview).filter(Boolean);
  return `
    ${parts.length ? `<div class="meta">${escapeHtml(parts.join(" / "))}</div>` : ""}
    ${evidence.length ? `<ul class="compact-list">${evidence.map((text) => `<li>${escapeHtml(text)}</li>`).join("")}</ul>` : ""}
  `;
}

function renderStageTrace(trace) {
  if (!trace || !trace.length) return `<div class="meta">暂无 stage trace</div>`;
  return `
    <ol class="trace-list">
      ${trace.map((stage) => `
        <li class="trace-step ${stage.status === "completed" || stage.status === "skipped" ? "trace-ok" : stage.status === "failed" ? "trace-failed" : ""}">
          <div>
            <strong>${escapeHtml(stage.stage)}</strong>
            <span class="tag">${escapeHtml(stage.status)}</span>
          </div>
          ${renderStageDetails(stage)}
        </li>
      `).join("")}
    </ol>
  `;
}

function renderCaseLLMLogs(logs) {
  if (!logs.length) return `<div class="meta">该 case 暂无关联 LLM 调用日志</div>`;
  return `
    <ul class="compact-list">
      ${logs.slice(0, 8).map((row) => `
        <li>
          <span class="tag">${escapeHtml(row.status)}</span>
          <span class="tag">${escapeHtml(row.context_json?.stage || "-")}</span>
          ${escapeHtml(row.trace_name)}
          <div class="meta">${escapeHtml(row.latency_ms)}ms / prompt=${escapeHtml(row.prompt_chars)} / response=${escapeHtml(row.response_chars)}${row.error_message ? ` / ${escapeHtml(row.error_message)}` : ""}</div>
        </li>
      `).join("")}
    </ul>
  `;
}

function renderLLMWorkflow(run, logs = []) {
  const summary = run?.summary_json || {};
  const cases = run?.case_results_json || [];
  if (!run || summary.evaluation_type !== "llm_workflow") {
    return `<div class="item meta">暂无真实 LLM workflow 结果</div>`;
  }
  const stats = llmLogStats(logs);
  return `
    <article class="item">
      <div class="item-title">
        <span>#${run.id} LLM workflow</span>
        <span class="status-pill ${summary.status === "completed" ? "ok" : ""}">${escapeHtml(summary.status || "recorded")}</span>
      </div>
      <div class="meta">case=${escapeHtml(summary.completed_cases ?? cases.length)} / remaining=${escapeHtml(summary.remaining_cases ?? "-")} / resume=${escapeHtml(summary.resume_from_last_completed || false)}</div>
      <div class="validation-panel ${summary.status === "completed" ? "validation-ok" : "validation-risk"}">
        <div class="validation-grid">${evaluationSummaryGrid(summary)}</div>
      </div>
      <div class="meta">当前 run LLM 日志：calls=${logs.length} / retry_1=${stats.retry1} / retry_2=${stats.retry2} / repair=${stats.repair} / failed=${stats.failed}</div>
      ${cases.map((item) => {
        const caseLogs = logs.filter((row) => row.context_json?.case_name === item.name);
        return `
        <section class="trace-card">
          <div class="item-title">
            <span>${escapeHtml(item.name)}</span>
            <span class="status-pill ${item.case_passed ? "ok" : ""}">${item.case_passed ? "passed" : escapeHtml(item.status || "failed")}</span>
          </div>
          <div class="meta">
            expected=${escapeHtml(item.expected_fit_label || "-")} / predicted=${escapeHtml(item.predicted_fit_label || "-")} / score=${escapeHtml(item.predicted_fit_score ?? "-")}
            ${item.failed_stage ? ` / failed_stage=${escapeHtml(item.failed_stage)}` : ""}
          </div>
          ${item.error ? `<div class="message-preview">${escapeHtml(item.error)}</div>` : ""}
          <h3>LLM 调用</h3>
          ${renderCaseLLMLogs(caseLogs)}
          <h3>Stage Trace</h3>
          ${renderStageTrace(item.stage_trace || [])}
        </section>
      `;
      }).join("")}
    </article>
  `;
}

async function loadEvaluationRuns() {
  const rows = await api("/evaluations/results");
  const latestInterviewSource = rows.find((row) => row.summary_json?.evaluation_type === "interview_source_smoke");
  const latestLLMWorkflow = rows.find((row) => row.summary_json?.evaluation_type === "llm_workflow");
  let llmLogs = [];
  if ($("#llm-workflow-result") && latestLLMWorkflow) {
    try {
      llmLogs = await api(`/llm/debug/logs?limit=500&evaluation_run_id=${Number(latestLLMWorkflow.id)}`);
    } catch (error) {
      llmLogs = [];
    }
  }
  const sourceTarget = $("#interview-source-smoke-result");
  if (sourceTarget) {
    sourceTarget.innerHTML = renderInterviewSourceSmoke(latestInterviewSource);
  }
  const llmTarget = $("#llm-workflow-result");
  if (llmTarget) {
    llmTarget.innerHTML = renderLLMWorkflow(latestLLMWorkflow, llmLogs);
  }
  renderItems("#evaluation-runs-list", rows, (row) => {
    const summary = row.summary_json || {};
    return `
      <article class="item">
        <div class="item-title">
          <span>#${row.id} ${escapeHtml(row.name)}</span>
          <span class="status-pill ${summary.status === "completed" ? "ok" : ""}">${escapeHtml(summary.status || "recorded")}</span>
        </div>
        <div class="meta">${escapeHtml(summary.evaluation_type || row.name)} / ${escapeHtml(row.created_at || "")}</div>
        <div class="validation-panel">
          <div class="validation-grid">${evaluationSummaryGrid(summary)}</div>
        </div>
      </article>
    `;
  });
  if (window.lucide) window.lucide.createIcons();
}

function renderTaskRun(row) {
  const progress = row.progress_json || {};
  const pct = Math.round(Number(progress.percent || 0) * 100);
  const isOk = row.status === "completed";
  const isRisk = row.status === "failed" || String(row.status || "").includes("fail");
  return `
    <article class="item">
      <div class="item-title">
        <span>#${row.id} ${escapeHtml(row.task_type)}</span>
        <span class="status-pill ${isOk ? "ok" : ""}">${escapeHtml(row.status)}</span>
      </div>
      <div class="progress-bar" aria-label="task progress"><span style="width:${Math.min(Math.max(pct, 0), 100)}%"></span></div>
      <div class="meta">
        ${pct}% / completed=${escapeHtml(progress.completed_cases ?? 0)} / remaining=${escapeHtml(progress.remaining_cases ?? "-")}
        ${progress.evaluation_run_id ? ` / evaluation_run_id=${escapeHtml(progress.evaluation_run_id)}` : ""}
      </div>
      ${progress.current_case ? `<div class="meta">current=${escapeHtml(progress.current_case)}</div>` : ""}
      ${row.error_message ? `<div class="message-preview">${escapeHtml(row.error_message)}</div>` : ""}
      <div class="validation-panel ${isRisk ? "validation-risk" : isOk ? "validation-ok" : ""}">
        <div class="validation-grid">
          <div><strong>E2E</strong><div class="meta">${formatPercent(progress.end_to_end_pass_rate)}</div></div>
          <div><strong>Fit</strong><div class="meta">${formatPercent(progress.fit_label_accuracy)}</div></div>
          <div><strong>Tailor</strong><div class="meta">${formatPercent(progress.tailor_pass_rate)}</div></div>
        </div>
      </div>
      <details class="details-block">
        <summary>任务详情</summary>
        <pre>${escapeHtml(safeJson({
          input: row.input_json,
          progress: row.progress_json,
          output: row.output_json,
          error: row.error_message,
          created_at: row.created_at,
          started_at: row.started_at,
          completed_at: row.completed_at,
        }))}</pre>
      </details>
    </article>
  `;
}

async function loadTasks() {
  const el = $("#task-runs-list");
  if (!el) return;
  const rows = await api("/tasks?limit=20");
  renderItems("#task-runs-list", rows, renderTaskRun);
  if (rows.some((row) => row.status === "queued" || row.status === "running")) {
    setTimeout(loadTasks, 5000);
  }
}

function prefillInterviewSourceImport(button) {
  const form = $("#interview-source-import-form");
  if (!form) return;
  const source = button.dataset.source || "";
  const title = button.dataset.title || "";
  const url = button.dataset.url || "";
  const snippet = button.dataset.snippet || "";
  const query = $("#interview-source-smoke-form")?.elements?.query?.value || "";
  form.source_site.value = sourceSiteLabel(source);
  form.source_url.value = url;
  form.title.value = title;
  if (query && form.role_keyword && !form.role_keyword.value) {
    form.role_keyword.value = query.replaceAll("面经", "").trim();
  }
  form.raw_text.value = [
    title,
    snippet,
    url ? `来源链接：${url}` : "",
    "请在导入前补充完整真实面经正文、轮次和追问；不要只保留搜索摘要。",
  ].filter(Boolean).join("\n");
  form.raw_text.focus();
  toast("已填入候选面经草稿，请人工补全后再导入");
}

function renderImportedInterviewExperience(row) {
  const questions = row.extracted_questions_json || [];
  const topics = row.topics_json || [];
  const params = new URLSearchParams();
  params.set("experience_ids", String(row.id));
  if (row.job_id) params.set("job_id", String(row.job_id));
  return `
    <article class="item">
      <div class="item-title">
        <span>已导入面经 #${row.id}</span>
        <span class="status-pill ${questions.length ? "ok" : ""}">${questions.length} questions</span>
      </div>
      <div class="meta">${escapeHtml(row.source_site)} / Job ${row.job_id || "-"} / ${escapeHtml(row.title || row.role_keyword || "")}</div>
      ${topics.length ? tags(topics) : ""}
      <p><a class="button ghost" href="/ui/interview-prep?${params.toString()}"><i data-lucide="messages-square"></i> 用该面经生成面试包</a></p>
      <ul class="compact-list">${questions.slice(0, 4).map((item) => `<li>${escapeHtml(item.question)}</li>`).join("")}</ul>
    </article>
  `;
}

function prefillInterviewPrepFromQuery() {
  const form = $("#interview-prep-form");
  if (!form) return;
  const params = new URLSearchParams(window.location.search);
  const experienceIds = params.get("experience_ids");
  const jobId = params.get("job_id");
  if (experienceIds && form.experience_ids) {
    form.experience_ids.value = experienceIds;
  }
  if (jobId && form.job_id) {
    form.job_id.value = jobId;
  }
}

function bindForms() {
  $("#admin-token-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formJson(event.currentTarget);
    const token = String(raw.admin_token || "").trim();
    if (token) {
      window.localStorage?.setItem(ADMIN_TOKEN_KEY, token);
      toast("管理令牌已保存");
    } else {
      window.localStorage?.removeItem(ADMIN_TOKEN_KEY);
      toast("管理令牌已清除");
    }
    updateAdminTokenState();
    await loadOpsPage();
  });

  $("#clear-admin-token")?.addEventListener("click", async () => {
    window.localStorage?.removeItem(ADMIN_TOKEN_KEY);
    updateAdminTokenState();
    toast("管理令牌已清除");
    await loadOpsPage();
  });

  $("#career-start-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await runCareerStartFlow(event.currentTarget);
  });

  document.querySelectorAll("[data-demo-scenario]").forEach((button) => button.addEventListener("click", () => {
    const form = $("#career-start-form");
    fillCareerDemo(form, button.dataset.demoScenario);
    updateStartInputGuidance(form);
  }));

  const careerStartForm = $("#career-start-form");
  if (careerStartForm) {
    updateStartInputGuidance(careerStartForm);
    updateSelectedProfileCard(null);
    careerStartForm.addEventListener("input", () => updateStartInputGuidance(careerStartForm));
    careerStartForm.addEventListener("change", async (event) => {
      updateStartInputGuidance(careerStartForm);
      if (event.target?.name === "resume_file") {
        const file = event.target.files?.[0];
        if (!file) {
          setPdfParseStatus(careerStartForm, "解析成功后会用于本次岗位匹配。");
          return;
        }
        try {
          await parseResumeFileIntoStartForm(careerStartForm, file);
        } catch (error) {
          setPdfParseStatus(careerStartForm, `PDF 解析失败：${error.message}`, "risk");
          toast(error.message);
        }
      }
    });
  }

  $("#open-profile-picker")?.addEventListener("click", () => {
    openProfilePicker().catch((error) => toast(error.message));
  });
  $("#profile-picker-search")?.addEventListener("input", renderProfilePickerList);
  document.querySelectorAll("[data-close-profile-picker]").forEach((button) => {
    button.addEventListener("click", closeProfilePicker);
  });

  $("#natural-profile-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = formJson(form);
    const result = $("#natural-profile-result");
    const submitButton = form.querySelector("button[type='submit']");
    if (submitButton) submitButton.disabled = true;
    if (result) result.innerHTML = `<article class="item meta">Agent 正在根据描述生成简历档案...</article>`;
    try {
      const body = await api("/assistant/natural-language", {
        method: "POST",
        body: JSON.stringify({
          instruction: raw.instruction,
          selected_actions: ["create_profile"],
          profile_context: null,
        }),
      });
      renderNaturalProfileResult(body);
      await loadProfiles();
      toast("简历档案已生成");
    } catch (error) {
      if (error.body?.run_id) {
        renderNaturalProfileResult(error.body);
      } else if (result) {
        result.innerHTML = `<article class="item validation-risk">${escapeHtml(error.message)}</article>`;
      }
      toast(error.message);
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });

  const guidedProfileForm = $("#guided-profile-form");
  if (guidedProfileForm) {
    initializeRepeatLists(guidedProfileForm);
    updateProfileSectionVisibility(guidedProfileForm);
    guidedProfileForm.querySelectorAll("[data-profile-section-toggle]").forEach((input) => {
      input.addEventListener("change", () => updateProfileSectionVisibility(guidedProfileForm));
    });
    guidedProfileForm.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const addButton = event.target.closest("[data-repeat-add]");
      if (addButton) {
        addRepeatEntry(guidedProfileForm, addButton.dataset.repeatAdd);
        return;
      }
      const removeButton = event.target.closest("[data-repeat-remove]");
      if (removeButton) {
        removeRepeatEntry(removeButton);
      }
    });
    guidedProfileForm.elements.photo_file?.addEventListener("change", async (event) => {
      try {
        await updateProfilePhotoPreview(event.currentTarget);
      } catch (error) {
        toast(error.message);
        event.currentTarget.value = "";
      }
    });
  }

  $("#upload-profile-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const response = await fetch("/profiles/upload", { method: "POST", body: data, headers: authHeaders() });
    if (!response.ok) throw new Error(await response.text());
    toast("Profile created");
    form.reset();
    loadProfiles();
  });

  $("#guided-profile-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = formJson(form);
    const enabledSections = selectedProfileSections(form);
    const payload = guidedProfilePayload(raw, enabledSections, form);
    if (enabledSections.includes("photo")) {
      payload.photo_data_url = await readProfilePhotoDataUrl(form);
    }
    await api("/profiles/guided", { method: "POST", body: JSON.stringify(payload) });
    toast("简历档案已保存");
    form.reset();
    resetRepeatLists(form);
    updateProfileSectionVisibility(form);
    const preview = $("#profile-photo-preview");
    if (preview) {
      preview.hidden = true;
      preview.removeAttribute("src");
    }
    loadProfiles();
  });

  $("#job-search-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await runJobDiscovery(event.currentTarget);
  });

  $("#open-job-search-profile-picker")?.addEventListener("click", () => {
    openJobSearchProfilePicker().catch((error) => toast(error.message));
  });
  $("#job-search-profile-picker-search")?.addEventListener("input", renderJobSearchProfilePicker);
  document.querySelectorAll("[data-close-job-search-profile-picker]").forEach((button) => {
    button.addEventListener("click", () => closeDialog("#job-search-profile-picker-dialog"));
  });

  $("#open-job-detail-profile-picker")?.addEventListener("click", () => {
    openJobDetailProfilePicker().catch((error) => toast(error.message));
  });
  $("#job-detail-profile-picker-search")?.addEventListener("input", renderJobDetailProfilePicker);
  document.querySelectorAll("[data-close-job-detail-profile-picker]").forEach((button) => {
    button.addEventListener("click", () => closeDialog("#job-detail-profile-picker-dialog"));
  });
  $("#job-detail-profile-upload")?.addEventListener("change", async (event) => {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    const status = $("#job-detail-profile-status");
    if (status) {
      status.textContent = "正在解析 PDF 并建立简历档案...";
      status.className = "field-hint running";
    }
    try {
      const profile = await uploadProfileFile(file);
      jobDetailProfileRows = [profile, ...jobDetailProfileRows.filter((item) => Number(item.id) !== Number(profile.id))];
      selectJobDetailProfile(profile);
      toast(`PDF 已解析为简历 #${profile.id}`);
    } catch (error) {
      if (status) {
        status.textContent = `PDF 解析失败：${error.message}`;
        status.className = "field-hint risk";
      }
      toast(error.message);
    }
  });
  $("#run-job-match")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await runJobDetailMatch();
      toast("岗位匹配与差距分析已完成");
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  });
  $("#run-job-tailor")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await runJobDetailTailor();
      toast("定制简历已生成");
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  });
  document.querySelectorAll("[data-job-tab]").forEach((button) => {
    button.addEventListener("click", () => activateJobDetailTab(button.dataset.jobTab));
  });

  $("#manual-job-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = formJson(form);
    await api("/jobs", { method: "POST", body: JSON.stringify(payload) });
    toast("Job created");
    form.reset();
    loadJobs();
  });

  $("#agent-run-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = formJson(form);
    const payload = {
      task_type: raw.task_type,
      profile_id: raw.profile_id ? Number(raw.profile_id) : null,
      job_id: raw.job_id ? Number(raw.job_id) : null,
      resume_version_id: raw.resume_version_id ? Number(raw.resume_version_id) : null,
      query: raw.query,
      limit: raw.limit ? Number(raw.limit) : 12,
    };
    const run = await api("/agent/runs", { method: "POST", body: JSON.stringify(payload) });
    toast(run.status === "waiting_for_confirmation" ? "流程等待确认" : "Agent run completed");
    loadRuns();
  });

  $("#dashboard-run-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formJson(event.currentTarget);
    const run = await api("/agent/runs", {
      method: "POST",
      body: JSON.stringify({
        task_type: "find_jobs_for_profile",
        profile_id: Number(raw.profile_id),
        query: raw.query,
        location: raw.location || null,
        limit: Number(raw.limit || 12),
      }),
    });
    const matches = run.output_json?.matches || [];
    renderItems("#dashboard-results", matches, (row) => `
      <article class="item">
        <div class="item-title"><span>${escapeHtml(row.title)}</span><span class="status-pill">${row.overall_score}</span></div>
        <div class="meta">${escapeHtml(row.company || "")}</div>
        ${tags(row.matched_skills || [])}
      </article>
    `);
    loadRecentRuns();
  });

  $("#tailor-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = formJson(form);
    const profileId = Number(raw.profile_id || 0);
    const jobId = Number(raw.job_id || 0);
    if (!profileId || !jobId) {
      throw new Error("请先选择简历档案和目标岗位。");
    }
    const reviewSlot = $("#tailor-review-result");
    const submitSlot = $("#tailor-submit-result");
    if (reviewSlot) reviewSlot.innerHTML = `<article class="item meta">正在做岗位针对性评分，评分和修改建议会单独显示，不会写入简历正文...</article>`;
    const review = await api(`/profiles/${profileId}/review`, {
      method: "POST",
      body: JSON.stringify({ job_id: jobId, include_llm: true }),
    });
    if (reviewSlot) {
      reviewSlot.innerHTML = renderResumeReview(review);
      if (window.lucide) window.lucide.createIcons();
    }
    if (submitSlot) submitSlot.innerHTML = `<article class="item meta">评分完成，正在生成定制简历...</article>`;
    const version = await api("/resumes/tailor", {
      method: "POST",
      body: JSON.stringify({ profile_id: profileId, job_id: jobId }),
    });
    if (submitSlot) {
      submitSlot.innerHTML = `
        <article class="item validation-ok">
          <div class="item-title"><span>定制简历 #${version.id}</span><span class="status-pill ${version.verification_json?.passed ? "ok" : "risk"}">${version.verification_json?.passed ? "事实检查通过" : "需检查"}</span></div>
          <p class="meta">评分、主要问题和修改建议已在上方单独展示；简历预览只包含可投递正文。</p>
          <div class="flow-result-actions">
            <a class="button ghost" href="/resumes/${version.id}/html" target="_blank"><i data-lucide="eye"></i> 打开 HTML 预览</a>
            <a class="button ghost" href="/resumes/${version.id}/markdown"><i data-lucide="download"></i> 下载 Markdown</a>
          </div>
        </article>
      `;
      if (window.lucide) window.lucide.createIcons();
    }
    toast("定制简历已生成");
    loadResumes();
  });

  $("#open-tailor-profile-picker")?.addEventListener("click", () => {
    openTailorProfilePicker().catch((error) => toast(error.message));
  });
  $("#open-tailor-job-picker")?.addEventListener("click", () => {
    openTailorJobPicker().catch((error) => toast(error.message));
  });
  $("#tailor-profile-picker-search")?.addEventListener("input", renderTailorProfilePickerList);
  $("#tailor-job-picker-search")?.addEventListener("input", renderTailorJobPickerList);
  document.querySelectorAll("[data-close-tailor-profile-picker]").forEach((button) => {
    button.addEventListener("click", () => closeDialog("#tailor-profile-picker-dialog"));
  });
  document.querySelectorAll("[data-close-tailor-job-picker]").forEach((button) => {
    button.addEventListener("click", () => closeDialog("#tailor-job-picker-dialog"));
  });

  $("#quick-apply-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formJson(event.currentTarget);
    await api("/applications/quick-apply", {
      method: "POST",
      body: JSON.stringify({
        profile_id: Number(raw.profile_id),
        job_id: Number(raw.job_id),
        resume_version_id: raw.resume_version_id ? Number(raw.resume_version_id) : null,
        browser_assist: event.currentTarget.browser_assist.checked,
      }),
    });
    toast("Application packet created");
    loadApplications();
  });

  $("#interview-prep-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = formJson(form);
    const payload = {
      profile_id: Number(raw.profile_id),
      job_id: Number(raw.job_id),
    };
    if (raw.experience_ids) {
      payload.experience_ids = raw.experience_ids
        .split(",")
        .map((x) => Number(x.trim()))
        .filter(Boolean);
    }
    await api("/interview-prep", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    toast("Interview prep created");
    form.reset();
    loadInterviewPreps();
  });

  $("#interview-experience-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = formJson(form);
    await api("/interview-prep/experiences", {
      method: "POST",
      body: JSON.stringify({
        job_id: raw.job_id ? Number(raw.job_id) : null,
        source_site: raw.source_site,
        source_url: raw.source_url || null,
        title: raw.title || null,
        company: raw.company || null,
        role_keyword: raw.role_keyword || null,
        raw_text: raw.raw_text,
      }),
    });
    toast("Interview experience imported");
    form.reset();
    loadInterviewExperiences();
  });

  $("#interview-practice-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formJson(event.currentTarget);
    await api(`/interview-prep/${Number(raw.prep_id)}/practice`, {
      method: "PUT",
      body: JSON.stringify({
        question_id: raw.question_id,
        status: raw.status || "todo",
        confidence_score: Number(raw.confidence_score || 0),
        notes: raw.notes || null,
      }),
    });
    toast("Practice status updated");
    loadInterviewPreps();
  });

  $("#interview-source-smoke-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formJson(event.currentTarget);
    const params = new URLSearchParams();
    params.set("query", raw.query || "Agent 开发实习生 面经");
    params.set("limit", String(Number(raw.limit || 5)));
    (raw.sources || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
      .forEach((source) => params.append("sources", source));
    await api(`/evaluations/interview-source-smoke?${params.toString()}`, { method: "POST" });
    toast("面经源探测完成");
    await loadEvaluationRuns();
  });

  $("#llm-workflow-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formJson(event.currentTarget);
    const params = new URLSearchParams();
    if (raw.case_limit) params.set("case_limit", String(Number(raw.case_limit)));
    if (event.currentTarget.resume_from_last_completed.checked) {
      params.set("resume_from_last_completed", "true");
    }
    const result = $("#llm-workflow-result");
    if (result) result.innerHTML = `<div class="item meta">真实 LLM workflow 运行中，完成后会展示逐 case trace...</div>`;
    await api(`/evaluations/llm-workflow?${params.toString()}`, { method: "POST" });
    toast("LLM workflow 评测完成");
    await loadEvaluationRuns();
  });

  $("#llm-workflow-task-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formJson(event.currentTarget);
    const params = new URLSearchParams();
    if (raw.case_limit) params.set("case_limit", raw.case_limit);
    if (raw.trace_path) params.set("trace_path", raw.trace_path);
    params.set("resume_from_last_completed", event.currentTarget.resume_from_last_completed.checked ? "true" : "false");
    const task = await api(`/tasks/llm-workflow?${params.toString()}`, { method: "POST" });
    toast(`后台任务已入队 #${task.id}`);
    await loadTasks();
  });

  $("#recover-queued-runs")?.addEventListener("click", async () => {
    const result = await api("/ops/queue/recover-queued", { method: "POST" });
    toast(`已恢复 queued run：${result.recovered_count || 0}`);
    await loadOpsPage();
  });

  $("#mark-stale-runs")?.addEventListener("click", async () => {
    const result = await api("/ops/agent-runs/mark-stale", { method: "POST" });
    toast(`已标记 stale run：${result.length || 0}`);
    await loadOpsPage();
  });

  $("#interview-source-import-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const raw = formJson(form);
    const created = await api("/interview-prep/experiences", {
      method: "POST",
      body: JSON.stringify({
        job_id: raw.job_id ? Number(raw.job_id) : null,
        source_site: raw.source_site,
        source_url: raw.source_url || null,
        title: raw.title || null,
        company: raw.company || null,
        role_keyword: raw.role_keyword || null,
        raw_text: raw.raw_text,
      }),
    });
    const result = $("#interview-source-import-result");
    if (result) {
      result.innerHTML = renderImportedInterviewExperience(created);
      if (window.lucide) window.lucide.createIcons();
    }
    toast("面经已导入，可在面试页引用");
    form.reset();
  });

  document.querySelectorAll("[data-refresh]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.refresh;
      if (key === "profiles") loadProfiles();
      if (key === "jobs") loadJobs();
      if (key === "runs") {
        loadRuns();
        loadRecentRuns();
      }
      if (key === "resumes") loadResumes();
      if (key === "applications") loadApplications();
      if (key === "interview-prep") loadInterviewPreps();
      if (key === "interview-experience") loadInterviewExperiences();
      if (key === "evaluations") loadEvaluationRuns();
      if (key === "tasks") loadTasks();
      if (key === "ops") loadOpsPage();
  });
  });

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const dismissRunButton = event.target.closest("[data-dismiss-active-run]");
    if (dismissRunButton) {
      dismissActiveRun(dismissRunButton.dataset.dismissActiveRun);
      return;
    }
    const toggleActiveRunsButton = event.target.closest("[data-toggle-active-runs]");
    if (toggleActiveRunsButton) {
      const monitor = $("#active-run-monitor");
      const collapsed = !monitor?.classList.contains("collapsed");
      window.localStorage?.setItem(ACTIVE_RUN_COLLAPSED_KEY, collapsed ? "1" : "0");
      monitor?.classList.toggle("collapsed", collapsed);
      const icon = toggleActiveRunsButton.querySelector("i");
      if (icon) icon.setAttribute("data-lucide", collapsed ? "chevron-up" : "chevron-down");
      toggleActiveRunsButton.title = collapsed ? "展开流程状态" : "收起流程状态";
      if (window.lucide) window.lucide.createIcons();
      return;
    }
    const importButton = event.target.closest("[data-import-interview-candidate]");
    if (importButton) prefillInterviewSourceImport(importButton);
    const reviewButton = event.target.closest("[data-review-profile]");
    if (reviewButton) {
      reviewButton.disabled = true;
      reviewProfile(reviewButton.dataset.reviewProfile)
        .catch((error) => toast(error.message))
        .finally(() => {
          reviewButton.disabled = false;
        });
    }
    const selectProfileButton = event.target.closest("[data-select-profile]");
    if (selectProfileButton) {
      selectProfileFromPicker(selectProfileButton.dataset.selectProfile);
    }
    const jobSearchProfileButton = event.target.closest("[data-select-job-search-profile]");
    if (jobSearchProfileButton) {
      const profile = jobSearchProfileRows.find(
        (item) => Number(item.id) === Number(jobSearchProfileButton.dataset.selectJobSearchProfile)
      );
      updateJobSearchProfile(profile);
      closeDialog("#job-search-profile-picker-dialog");
      toast(`已选择简历 #${profile?.id}`);
    }
    const jobDetailProfileButton = event.target.closest("[data-select-job-detail-profile]");
    if (jobDetailProfileButton) {
      const profile = jobDetailProfileRows.find(
        (item) => Number(item.id) === Number(jobDetailProfileButton.dataset.selectJobDetailProfile)
      );
      selectJobDetailProfile(profile);
      closeDialog("#job-detail-profile-picker-dialog");
      toast(`已选择简历 #${profile?.id}`);
    }
    const tailorProfileButton = event.target.closest("[data-select-tailor-profile]");
    if (tailorProfileButton) {
      const profile = profilePickerRows.find((item) => Number(item.id) === Number(tailorProfileButton.dataset.selectTailorProfile));
      updateTailorProfileCard(profile);
      closeDialog("#tailor-profile-picker-dialog");
      toast(`已选择简历 #${tailorProfileButton.dataset.selectTailorProfile}`);
    }
    const tailorJobButton = event.target.closest("[data-select-tailor-job]");
    if (tailorJobButton) {
      const job = jobPickerRows.find((item) => Number(item.id) === Number(tailorJobButton.dataset.selectTailorJob));
      updateTailorJobCard(job);
      closeDialog("#tailor-job-picker-dialog");
      toast(`已选择岗位 #${tailorJobButton.dataset.selectTailorJob}`);
    }
    const resumeRunButton = event.target.closest("[data-resume-run-id]");
    if (resumeRunButton) {
      const runId = resumeRunButton.dataset.resumeRunId;
      resumeRunButton.disabled = true;
      resumeAgentRun(runId, {
        confirmed: true,
        note: "用户在历史记录详情中确认继续生成投递包。",
        resume_json: { source: "agent_runs_detail" },
      })
        .then((resumed) => {
          toast(resumed.status === "completed" ? "已确认并继续完成" : `当前状态：${resumed.status}`);
          return loadRuns();
        })
        .catch((error) => toast(error.message))
        .finally(() => {
          resumeRunButton.disabled = false;
        });
    }
    const qualityButton = event.target.closest("[data-quality-jump]");
    if (qualityButton) focusInterviewQuestion(qualityButton);
    const cancelButton = event.target.closest("[data-cancel-run]");
    if (cancelButton) {
      const runId = cancelButton.dataset.cancelRun;
      api(`/agent/runs/${runId}/cancel`, {
        method: "POST",
        body: JSON.stringify({ reason: "管理员在控制台取消运行" }),
      })
        .then(() => {
          toast(`已取消 run #${runId}`);
          return loadOpsPage();
        })
        .catch((error) => toast(error.message));
    }
    const approvalButton = event.target.closest("[data-approval-decision]");
    if (approvalButton) {
      const approvalId = approvalButton.dataset.approvalDecision;
      const approved = approvalButton.dataset.approved === "true";
      api(`/ops/approvals/${approvalId}/decision`, {
        method: "POST",
        body: JSON.stringify({
          approved,
          note: approved ? "管理员在控制台审批通过" : "管理员在控制台审批拒绝",
          decided_by_user_id: "ops_console",
        }),
      })
        .then(() => {
          toast(`审批 #${approvalId} 已${approved ? "通过" : "拒绝"}`);
          return loadOpsPage();
        })
        .catch((error) => toast(error.message));
    }
    const dlqReplayButton = event.target.closest("[data-dlq-replay]");
    if (dlqReplayButton) {
      const index = dlqReplayButton.dataset.dlqReplay;
      api(`/ops/queue/dead-letter/${index}/replay?actor=ops_console`, { method: "POST" })
        .then(() => {
          toast(`DLQ #${index} 已重放`);
          return loadOpsPage();
        })
        .catch((error) => toast(error.message));
    }
    const dlqDiscardButton = event.target.closest("[data-dlq-discard]");
    if (dlqDiscardButton) {
      const index = dlqDiscardButton.dataset.dlqDiscard;
      api(`/ops/queue/dead-letter/${index}/discard?actor=ops_console`, { method: "POST" })
        .then(() => {
          toast(`DLQ #${index} 已丢弃`);
          return loadOpsPage();
        })
        .catch((error) => toast(error.message));
    }
  });
}

async function bootstrap() {
  bindForms();
  const page = document.body.dataset.page;
  try {
    await loadGlobalLLMWarning();
    if (page === "dashboard") {
      await loadHealth();
    }
    if (page === "profiles") await loadProfiles();
    if (page === "jobs") await loadJobs();
    if (page === "agent_runs") await loadRuns();
    if (page === "resumes") await loadResumes();
    if (page === "applications") await loadApplications();
    if (page === "interview_prep") {
      prefillInterviewPrepFromQuery();
      await loadInterviewExperiences();
      await loadInterviewPreps();
    }
    if (page === "evaluations") await loadEvaluationRuns();
    if (page === "evaluations") await loadTasks();
    if (page === "ops") await loadOpsPage();
    await restoreActiveRuns();
  } catch (error) {
    toast(error.message);
  }
  if (window.lucide) window.lucide.createIcons();
}

bootstrap();
