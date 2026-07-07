const $ = (selector) => document.querySelector(selector);
const ADMIN_TOKEN_KEY = "careeragent.admin_token";
let activeRunEventSource = null;

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
    obj[key] = value;
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
  pill.classList.add("ok");
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
      </div>
    </article>
  `);
}

async function loadJobs() {
  const rows = await api("/jobs");
  renderItems("#jobs-list", rows, (row) => `
    <article class="item">
      <div class="item-title"><span>#${row.id} ${escapeHtml(row.title)}</span><span class="meta">${escapeHtml(row.company || "未知公司")}</span></div>
      <div class="meta">${escapeHtml(row.company || "")} ${escapeHtml(row.location || "")}</div>
      ${tags(row.structured_jd_json.required_skills || row.structured_jd_json.keywords || [])}
      ${row.apply_url ? `<p><a class="button ghost" href="${escapeHtml(row.apply_url)}" target="_blank"><i data-lucide="external-link"></i> 打开投递页</a></p>` : ""}
    </article>
  `);
}

async function loadRuns(target = "#runs-list") {
  const rows = await api("/agent/runs");
  renderItems(target, rows, (row) => `
    <article class="item">
      <div class="item-title">
        <button class="ghost" data-run-id="${row.id}">#${row.id} ${escapeHtml(taskLabel(row.task_type))}</button>
        <span class="status-pill ${row.status === "completed" ? "ok" : row.status === "failed" ? "risk" : ""}">${row.status === "completed" ? "已完成" : row.status === "failed" ? "失败" : escapeHtml(row.status)}</span>
      </div>
      <div class="meta">简历 ${row.profile_id || "-"} · 岗位 ${row.job_id || "-"} · ${row.latency_ms}ms</div>
      ${renderRunOutcomeLinks(row)}
    </article>
  `);
  document.querySelectorAll("[data-run-id]").forEach((button) => {
    button.addEventListener("click", () => loadRunSteps(button.dataset.runId));
  });
  document.querySelectorAll("[data-resume-run-id]").forEach((button) => {
    button.addEventListener("click", async () => {
      const runId = button.dataset.resumeRunId;
      const resumed = await resumeAgentRun(runId, {
        confirmed: true,
        note: "用户在流程页面确认继续生成投递包。",
        resume_json: { source: "agent_runs_page" },
      });
      toast(resumed.status === "completed" ? "已确认并继续完成" : `当前状态：${resumed.status}`);
      loadRuns(target);
      loadRunSteps(runId);
    });
  });
}

function renderRunOutcomeLinks(row) {
  const output = row.output_json || {};
  const links = [];
  const resumeId = output.resume_version_id || output.tailor?.resume_version_id;
  const applicationId = output.application_id || output.application?.application_id;
  const prepId = output.interview_prep_id || output.interview_prep?.interview_prep_id;
  if (resumeId) links.push(`<a class="button ghost" href="/ui/resumes"><i data-lucide="file-check-2"></i> 简历 #${resumeId}</a>`);
  if (applicationId) links.push(`<a class="button ghost" href="/ui/applications"><i data-lucide="send"></i> 投递包 #${applicationId}</a>`);
  if (prepId) links.push(`<a class="button ghost" href="/ui/prep"><i data-lucide="messages-square"></i> 面试包 #${prepId}</a>`);
  if (output.matches?.length) links.push(`<a class="button ghost" href="/ui/jobs"><i data-lucide="briefcase-business"></i> 推荐岗位 ${output.matches.length} 个</a>`);
  if (output.requires_confirmation) links.push(`<button class="button primary" data-resume-run-id="${row.id}" type="button"><i data-lucide="check-circle-2"></i> 确认继续</button>`);
  return links.length ? `<div class="flow-result-actions">${links.join("")}</div>` : "";
}

async function loadRunSteps(runId) {
  const rows = await api(`/agent/runs/${runId}/steps`);
  renderItems("#run-steps", rows, (row) => `
    <article class="item">
      <div class="item-title"><span>${escapeHtml(stepLabel(row.step_name))}</span><span class="status-pill ${row.status === "completed" ? "ok" : row.status === "failed" ? "risk" : ""}">${row.status === "completed" ? "完成" : row.status === "failed" ? "失败" : escapeHtml(row.status)}</span></div>
      <div class="meta">${row.latency_ms}ms${row.error_message ? ` · ${escapeHtml(row.error_message)}` : ""}</div>
    </article>
  `);
  await loadRunEvents(runId);
  subscribeAgentRunEvents(runId);
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
  el.innerHTML = rows.map((row) => `
    <article class="resume-card">
      <div class="item-title"><span>#${row.id} ${escapeHtml(row.title)}</span><span class="status-pill ${row.verification_json.passed ? "ok" : "risk"}">${row.verification_json.passed ? "事实检查通过" : "需检查"}</span></div>
      <p class="meta">简历 ${row.profile_id} · 岗位 ${row.job_id}</p>
      <iframe class="resume-preview-frame" src="/resumes/${row.id}/html" title="定制简历 #${row.id} 预览"></iframe>
      <div class="flow-result-actions">
        <a class="button ghost" href="/resumes/${row.id}/html" target="_blank"><i data-lucide="eye"></i> 打开 HTML 预览</a>
        <a class="button ghost" href="/resumes/${row.id}/markdown"><i data-lucide="download"></i> 下载 Markdown</a>
      </div>
    </article>
  `).join("");
  if (window.lucide) window.lucide.createIcons();
}

async function loadApplications() {
  const rows = await api("/applications");
  renderItems("#applications-list", rows, (row) => `
    <article class="item">
      <div class="item-title"><span>#${row.id} 岗位 ${row.job_id}</span><span class="status-pill ${row.status === "ready" ? "ok" : ""}">${row.status === "ready" ? "准备好了" : escapeHtml(row.status)}</span></div>
      <div class="meta">定制简历 ${row.resume_version_id || "-"}</div>
      ${row.apply_url ? `<p><a class="button ghost" href="${escapeHtml(row.apply_url)}" target="_blank"><i data-lucide="external-link"></i> 打开投递页</a></p>` : ""}
      ${applicationValidation(row)}
      ${row.outreach_message ? `<p class="message-preview">${escapeHtml(row.outreach_message)}</p>` : ""}
      <pre>${escapeHtml(row.cover_letter || "")}</pre>
    </article>
  `);
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
  return `
    <article class="item">
      <div class="validation-grid">
        ${metricCell("队列", queue.queue_name || "-")}
        ${metricCell("queued", queue.queued_count ?? "-")}
        ${metricCell("DLQ", queue.dead_letter_count ?? "-")}
        ${metricCell("最大重试", queue.worker_max_attempts ?? "-")}
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

const CAREER_FLOW_STAGES = ["profile", "search", "match", "tailor", "apply", "interview"];

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
  result.innerHTML = `
    <article class="item flow-result-card">
      <div class="item-title">
        <span>${escapeHtml(selected.title || "已完成")}</span>
        <span class="status-pill ok">${escapeHtml(selected.overall_score ?? "ready")}</span>
      </div>
      <div class="meta">${escapeHtml(selected.company || "")} / Profile ${escapeHtml(state.profile?.id || "-")} / Job ${escapeHtml(selected.job_id || "-")}</div>
      ${tags(selected.matched_skills || [])}
      <div class="flow-result-actions">
        ${tailor.resume_version_id ? `<a class="button ghost" href="/ui/resumes"><i data-lucide="file-check-2"></i> 简历版本 #${tailor.resume_version_id}</a>` : ""}
        ${apply.application_id ? `<a class="button ghost" href="/ui/applications"><i data-lucide="send"></i> 投递包 #${apply.application_id}</a>` : ""}
        ${interview.interview_prep_id ? `<a class="button ghost" href="/ui/prep?job_id=${escapeHtml(selected.job_id || "")}"><i data-lucide="messages-square"></i> 面试包 #${interview.interview_prep_id}</a>` : ""}
        ${careerFlowRunLink(state.fullRun, "完整流程")}
        ${careerFlowRunLink(state.searchRun, "找岗")}
        ${careerFlowRunLink(state.tailorRun, "定制")}
        ${careerFlowRunLink(state.applyRun, "投递")}
        ${careerFlowRunLink(state.interviewRun, "面试")}
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
    return await uploadProfileFile(file);
  }
  if (!raw.name) {
    throw new Error("请上传 PDF、填写核心简历信息或输入已有 Profile ID。");
  }
  return await api("/profiles/guided", { method: "POST", body: JSON.stringify(guidedProfilePayload(raw)) });
}

async function uploadProfileFile(file) {
  const data = new FormData();
  data.append("file", file);
  const response = await fetch("/profiles/upload", { method: "POST", body: data, headers: authHeaders() });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
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
  if (run.status === "waiting_for_confirmation" && options.autoConfirmApplication) {
    toast("投递包生成前需要确认，已按一键流程继续。");
    const resumed = await resumeAgentRun(run.id, {
      confirmed: true,
      note: options.confirmationNote || "用户在一键流程中确认生成投递包。",
      resume_json: { source: "frontend_auto_confirm" },
    });
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
  return api("/agent/runs/background", { method: "POST", body: JSON.stringify(payload) });
}

function updateCareerFlowFromEvent(event) {
  const node = event.node_name || event.event_json?.node_name || "";
  const type = event.event_type || "";
  const running = type.includes("started") || type.includes("update");
  const done = type.includes("completed");
  const status = running ? "running" : done ? "done" : "";
  if (!status) return;
  const map = {
    search_jobs: "search",
    match_jobs: "match",
    select_job: "match",
    match_job: "match",
    tailor_resume: "tailor",
    fit_gate: "apply",
    ensure_resume_version: "apply",
    create_application_packet: "apply",
    generate_interview_prep: "interview",
  };
  const stage = map[node];
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
        if (run.status === "completed") resolve(run);
        else reject(new Error(run.error_message || run.output_json?.error || `流程状态：${run.status}`));
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
  const state = {};
  try {
    const raw = formJson(form);
    setCareerStage("profile", "running", "建档中");
    state.profile = await createProfileForCareerFlow(form, raw);
    setCareerStage("profile", "done", `Profile #${state.profile.id}`);

    setCareerStage("search", "running", raw.job_id || raw.jd_text ? "读取目标岗位" : "搜索真实岗位");
    const directJob = await resolveDirectJobForCareerFlow(raw, state.profile.id);
    if (directJob) {
      state.selectedJob = directJob;
      setCareerStage("search", "done", `Job #${directJob.job_id}`);
    } else {
      setCareerStage("search", "running", "LangGraph 搜索真实岗位");
    }

    setCareerStage("match", "running", "LangGraph 匹配岗位");
    const queuedRun = await createBackgroundAgentRun(
      {
        task_type: "full_career_flow",
        profile_id: Number(state.profile.id),
        job_id: state.selectedJob?.job_id ? Number(state.selectedJob.job_id) : null,
        query: raw.query || "Agent 开发实习生",
        location: raw.location || null,
        limit: Number(raw.limit || 8),
      }
    );
    state.fullRun = await waitForAgentRun(
      queuedRun.id,
      {
        autoConfirmApplication: true,
        confirmationNote: "用户在首页一键流程中确认生成投递包。",
      }
    );
    const output = state.fullRun.output_json || {};
    state.selectedJob = output.selected_job || state.selectedJob || topMatchedJob(state.fullRun);
    state.tailorRun = { id: state.fullRun.id, output_json: output.tailor || {} };
    state.applyRun = { id: state.fullRun.id, output_json: output.application || {} };
    state.interviewRun = { id: state.fullRun.id, output_json: output.interview_prep || {} };
    setCareerStage("search", "done", state.selectedJob?.job_id ? `Job #${state.selectedJob.job_id}` : `Run #${state.fullRun.id}`);
    setCareerStage("match", "done", `${state.selectedJob?.company || ""} ${state.selectedJob?.overall_score || ""}`);
    setCareerStage("tailor", "done", `版本 #${state.tailorRun.output_json?.resume_version_id || "-"}`);
    setCareerStage("apply", "done", `投递包 #${state.applyRun.output_json?.application_id || "-"}`);
    setCareerStage("interview", "done", `面试包 #${state.interviewRun.output_json?.interview_prep_id || "-"}`);
    renderCareerFlowResult(state);
    toast("完整求职流程已完成");
  } catch (error) {
    const current = CAREER_FLOW_STAGES.find((stage) => document.querySelector(`#career-flow-steps [data-stage="${stage}"]`)?.classList.contains("running"));
    if (current) setCareerStage(current, "failed", "失败");
    renderCareerFlowMessage("error", error.message);
    toast(error.message);
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
}

function fillCareerDemo(form) {
  if (!form) return;
  form.profile_id.value = "";
  form.name.value = "李明";
  form.email.value = "liming@example.com";
  form.target_roles.value = "Agent 开发实习生, AI 应用开发实习生";
  form.skills.value = "Python, FastAPI, SQLite, RAG, LLM API, Agent workflow, Evaluation, Guardrails, 流程追踪, Playwright";
  form.project.value = [
    "CareerAgent：面向中文求职场景的 Agent 求职助手。",
    "实现 PDF Chunk、SQLite RAG、岗位匹配、定制简历、投递包、面试准备和全链路过程记录。",
    "使用 Python、FastAPI、SQLite、LLM API、Plan-Execute、ReAct repair、Guardrails、Playwright 和 pytest 支撑真实流程测试。"
  ].join("\n");
  form.query.value = "Agent 开发实习生";
  form.location.value = "深圳";
  form.limit.value = "8";
  if (form.company) form.company.value = "DemoAI";
  if (form.job_id) form.job_id.value = "";
  if (form.jd_text) {
    form.jd_text.value = [
      "岗位：Agent 开发实习生",
      "职责：参与 Agent workflow、RAG 检索、工具调用、LLM 调用记录、简历定制、投递包和面试准备链路开发。",
      "要求：熟悉 Python、FastAPI、SQLite、RAG、LLM API、Evaluation、Guardrails、Plan-Execute 和 ReAct repair。",
      "加分：有可上线的 Agent 项目、前端交互优化和真实 LLM 调试经验。"
    ].join("\n");
  }
  toast("已填入演示信息");
}

function renderNaturalLanguageResult(body) {
  const result = $("#natural-language-result");
  if (!result) return;
  const data = body.result_json || {};
  const runs = data.agent_runs || [];
  const failed = body.status === "failed";
  const links = [];
  if (body.run_id) links.push(`<a class="button ghost" href="/ui/agent-runs"><i data-lucide="route"></i> 查看流程 #${body.run_id}</a>`);
  if (data.profile?.id) links.push(`<a class="button ghost" href="/ui/profiles"><i data-lucide="file-user"></i> 简历 #${data.profile.id}</a>`);
  if (data.job?.id) links.push(`<a class="button ghost" href="/ui/jobs"><i data-lucide="briefcase-business"></i> 岗位 #${data.job.id}</a>`);
  if (data.tailor?.resume_version_id) links.push(`<a class="button ghost" href="/ui/resumes"><i data-lucide="file-check-2"></i> 定制简历 #${data.tailor.resume_version_id}</a>`);
  if (data.application?.application_id) links.push(`<a class="button ghost" href="/ui/applications"><i data-lucide="send"></i> 投递包 #${data.application.application_id}</a>`);
  if (data.interview_prep?.interview_prep_id) links.push(`<a class="button ghost" href="/ui/prep"><i data-lucide="messages-square"></i> 面试包 #${data.interview_prep.interview_prep_id}</a>`);
  if (data.matches?.length) links.push(`<a class="button ghost" href="/ui/jobs"><i data-lucide="search"></i> 推荐岗位 ${data.matches.length} 个</a>`);
  runs.forEach((run) => links.push(`<a class="button ghost" href="/ui/agent-runs"><i data-lucide="route"></i> ${escapeHtml(taskLabel(run.task_type))} #${run.id}</a>`));
  result.innerHTML = `
    <article class="item flow-result-card">
      <div class="item-title">
        <span>${escapeHtml(body.user_message || (failed ? "处理失败" : "处理完成"))}</span>
        <span class="status-pill ${failed ? "risk" : "ok"}">${failed ? "需处理" : "已完成"}</span>
      </div>
      <div class="meta">需求 Run #${escapeHtml(body.run_id)}${body.repair_attempts?.length ? ` · 自动修复 ${body.repair_attempts.length} 次` : ""}</div>
      <div class="flow-result-actions">${links.join("")}</div>
    </article>
  `;
  if (window.lucide) window.lucide.createIcons();
}

async function runNaturalLanguageRequest(form) {
  const result = $("#natural-language-result");
  const submitButton = form.querySelector("button[type='submit']");
  if (submitButton) submitButton.disabled = true;
  if (result) result.innerHTML = `<article class="item meta">Agent 正在理解需求并执行，复杂任务可能需要几十秒...</article>`;
  try {
    const raw = formJson(form);
    const file = form.elements.resume_file?.files?.[0];
    let profileId = raw.profile_id ? Number(raw.profile_id) : null;
    if (file) {
      const profile = await uploadProfileFile(file);
      profileId = profile.id;
    }
    const body = await api("/assistant/natural-language", {
      method: "POST",
      body: JSON.stringify({
        instruction: raw.instruction,
        profile_id: profileId,
        job_id: raw.job_id ? Number(raw.job_id) : null,
        jd_text: raw.jd_text || null,
        location: raw.location || null,
        query: raw.query || "Agent 开发实习生",
        limit: 8,
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

function fillNaturalDemo(form) {
  if (!form) return;
  form.instruction.value = "我想找 Agent 开发实习岗位。请根据下面 JD 帮我生成一份简历档案，并针对这个岗位改简历、生成投递包和面试准备问题。我的项目是 CareerAgent：用 Python、FastAPI、SQLite、RAG、LLM API、Plan-Execute、ReAct repair、Guardrails 和前端页面做了一个真实可用的求职助手。";
  form.profile_id.value = "";
  form.job_id.value = "";
  form.location.value = "深圳";
  form.jd_text.value = [
    "岗位：Agent 开发实习生",
    "职责：参与 Agent workflow、RAG 检索、工具调用、LLM 调用记录、简历定制、投递包和面试准备链路开发。",
    "要求：熟悉 Python、FastAPI、SQLite、RAG、LLM API、Evaluation、Guardrails、Plan-Execute 和 ReAct repair。"
  ].join("\n");
  toast("已填入自然语言示例");
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

  $("#natural-language-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await runNaturalLanguageRequest(event.currentTarget);
  });

  $("#natural-demo-fill")?.addEventListener("click", () => {
    fillNaturalDemo($("#natural-language-form"));
  });

  $("#career-start-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await runCareerStartFlow(event.currentTarget);
  });

  $("#career-demo-fill")?.addEventListener("click", () => {
    fillCareerDemo($("#career-start-form"));
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
    const raw = formJson(event.currentTarget);
    const payload = {
      query: raw.query,
      location: raw.location || null,
      limit: Number(raw.limit || 20),
      internship_only: event.currentTarget.internship_only.checked,
      sources: ["tencent"],
      store_results: true,
    };
    const body = await api("/jobs/search", { method: "POST", body: JSON.stringify(payload) });
    const errors = $("#job-search-errors");
    if (errors) errors.textContent = Object.entries(body.source_errors || {}).map(([k, v]) => `${k}: ${v}`).join(" | ");
    toast(`Imported ${body.jobs.length} jobs`);
    loadJobs();
  });

  $("#manual-job-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = formJson(event.currentTarget);
    await api("/jobs", { method: "POST", body: JSON.stringify(payload) });
    toast("Job created");
    event.currentTarget.reset();
    loadJobs();
  });

  $("#agent-run-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formJson(event.currentTarget);
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
    const raw = formJson(event.currentTarget);
    await api("/resumes/tailor", {
      method: "POST",
      body: JSON.stringify({ profile_id: Number(raw.profile_id), job_id: Number(raw.job_id) }),
    });
    toast("Resume tailored");
    loadResumes();
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
    const raw = formJson(event.currentTarget);
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
    event.currentTarget.reset();
    loadInterviewPreps();
  });

  $("#interview-experience-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formJson(event.currentTarget);
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
    event.currentTarget.reset();
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
    const raw = formJson(event.currentTarget);
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
    event.currentTarget.reset();
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
    const importButton = event.target.closest("[data-import-interview-candidate]");
    if (importButton) prefillInterviewSourceImport(importButton);
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
  } catch (error) {
    toast(error.message);
  }
  if (window.lucide) window.lucide.createIcons();
}

bootstrap();
