const $ = (selector) => document.querySelector(selector);

function toast(message) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
  setTimeout(() => {
    el.hidden = true;
  }, 3600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = await response.text();
    try {
      detail = JSON.parse(detail).detail || detail;
    } catch (_) {
      // keep text
    }
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

function interviewSourceLabel(source) {
  const labels = {
    source_backed_interview_experience: "已导入面经",
    online_experience_research: "同岗面经调研",
    resume_project_evidence: "项目证据",
    resume_project_stack: "项目技术栈",
    jd_technical_depth: "JD 技术",
    jd_gap_drill: "缺口追问",
    general_interview: "通用问题",
  };
  return labels[source] || source || "未标注";
}

function validationList(items, emptyText) {
  if (!items || !items.length) return `<div class="meta">${escapeHtml(emptyText)}</div>`;
  return `<ul class="compact-list">${items.map((item) => `
    <li><span class="tag">${escapeHtml(item.code || "note")}</span>${escapeHtml(item.message || "")}${item.terms?.length ? `：${escapeHtml(item.terms.join("、"))}` : ""}</li>
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
        <span class="status-pill ${passed ? "ok" : ""}">${escapeHtml(risk)}</span>
        <span class="meta">${escapeHtml(automation.mode || "manual_confirm_required")} · ${escapeHtml(automation.final_submission || "user_confirmed_only")}</span>
      </div>
      <div class="validation-grid">
        <div>
          <strong>Issues</strong>
          ${validationList(validation.issues || [], "未发现阻断问题")}
        </div>
        <div>
          <strong>Warnings</strong>
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
    if (value === "") continue;
    obj[key] = value;
  }
  return obj;
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
      <div class="item-title"><span>#${row.id} ${escapeHtml(row.name || "Unnamed")}</span><span class="meta">${escapeHtml(row.source_type)}</span></div>
      <div class="meta">${escapeHtml(row.headline || "")}</div>
      ${tags(row.structured_profile_json.skills || [])}
    </article>
  `);
}

async function loadJobs() {
  const rows = await api("/jobs");
  renderItems("#jobs-list", rows, (row) => `
    <article class="item">
      <div class="item-title"><span>#${row.id} ${escapeHtml(row.title)}</span><span class="meta">${escapeHtml(row.source)}</span></div>
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
      <div class="item-title"><button class="ghost" data-run-id="${row.id}">#${row.id} ${escapeHtml(row.task_type)}</button><span class="status-pill">${escapeHtml(row.status)}</span></div>
      <div class="meta">profile=${row.profile_id || "-"} job=${row.job_id || "-"} latency=${row.latency_ms}ms</div>
    </article>
  `);
  document.querySelectorAll("[data-run-id]").forEach((button) => {
    button.addEventListener("click", () => loadRunSteps(button.dataset.runId));
  });
}

async function loadRunSteps(runId) {
  const rows = await api(`/agent/runs/${runId}/steps`);
  renderItems("#run-steps", rows, (row) => `
    <article class="item">
      <div class="item-title"><span>${escapeHtml(row.step_name)}</span><span class="status-pill">${escapeHtml(row.status)}</span></div>
      <div class="meta">${escapeHtml(row.tool_name || "")} ${row.latency_ms}ms</div>
      <pre>${escapeHtml(JSON.stringify(row.output_json || row.error_message || {}, null, 2))}</pre>
    </article>
  `);
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
      <div class="item-title"><span>#${row.id} ${escapeHtml(row.title)}</span><span class="status-pill">${escapeHtml(row.verification_json.risk_level || "unknown")}</span></div>
      <p class="meta">Profile ${row.profile_id} · Job ${row.job_id}</p>
      <pre>${escapeHtml(row.tailored_resume_markdown)}</pre>
      <a class="button ghost" href="/resumes/${row.id}/markdown"><i data-lucide="download"></i> Markdown</a>
    </article>
  `).join("");
  if (window.lucide) window.lucide.createIcons();
}

async function loadApplications() {
  const rows = await api("/applications");
  renderItems("#applications-list", rows, (row) => `
    <article class="item">
      <div class="item-title"><span>#${row.id} Job ${row.job_id}</span><span class="status-pill">${escapeHtml(row.status)}</span></div>
      <div class="meta">Resume Version ${row.resume_version_id || "-"}</div>
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
    const coreSources = coverage.core_perspective_counts || {};
    const questionSets = row.question_sets_json || [];
    const drills = row.gap_drills_json || [];
    const research = row.research_checklist_json || [];
    return `
      <article class="item">
        <div class="item-title">
          <span>#${row.id} ${escapeHtml(row.title)}</span>
          <span class="status-pill ${coverage.passed ? "ok" : ""}">${coverage.passed ? "ready" : "review"}</span>
        </div>
        <div class="meta">Profile ${row.profile_id} / Job ${row.job_id} / ${escapeHtml(summary.fit_level || "unknown")} / score ${escapeHtml(summary.overall_score ?? "-")}</div>
        <p><a class="button ghost" href="/interview-prep/${row.id}/markdown"><i data-lucide="download"></i> Markdown</a></p>
        <div class="validation-panel ${coverage.passed ? "validation-ok" : "validation-risk"}">
          <div class="validation-grid">
            <div><strong>题目数</strong><div class="meta">${coverage.question_count || 0}</div></div>
            <div><strong>必备技能覆盖</strong><div class="meta">${Math.round((coverage.required_skill_coverage_rate || 0) * 100)}%</div></div>
            <div><strong>缺口 Drill</strong><div class="meta">${coverage.gap_drill_count || 0}</div></div>
            <div><strong>证据题占比</strong><div class="meta">${Math.round((coverage.evidence_backed_question_rate || 0) * 100)}%</div></div>
            <div><strong>面经角度</strong><div class="meta">${coreSources.online_experience || 0}</div></div>
            <div><strong>项目技术栈</strong><div class="meta">${coreSources.resume_project_stack || 0}</div></div>
            <div><strong>其他问题</strong><div class="meta">${coreSources.other_interview_questions || 0}</div></div>
          </div>
        </div>
        ${questionSets.map((group) => `
          <h3>${escapeHtml(group.category)}</h3>
          <ul class="compact-list">${(group.questions || []).slice(0, 4).map((q) => `
            <li><span class="tag">${escapeHtml(q.question_id || "-")}</span><span class="tag">${escapeHtml(interviewSourceLabel(q.source_perspective))}</span><span class="tag">${escapeHtml(q.risk_level || "low")}</span>${escapeHtml(q.question)}</li>
          `).join("")}</ul>
        `).join("")}
        ${drills.length ? `<h3>缺口 Drill</h3><ul class="compact-list">${drills.slice(0, 5).map((item) => `<li><span class="tag">${escapeHtml(item.skill)}</span>${escapeHtml(item.honest_strategy)}</li>`).join("")}</ul>` : ""}
        ${research.length ? `<h3>外部调研清单</h3><ul class="compact-list">${research.map((item) => `<li><span class="tag">${escapeHtml(item.site || item.topic)}</span>${escapeHtml(item.topic)}：${escapeHtml(item.query)}</li>`).join("")}</ul>` : ""}
      </article>
    `;
  });
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
          <span class="status-pill ${questions.length ? "ok" : ""}">${questions.length} questions</span>
        </div>
        <div class="meta">${escapeHtml(row.source_site)} / Job ${row.job_id || "-"} / credibility ${escapeHtml(credibility.score ?? "-")}</div>
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

async function loadEvaluationRuns() {
  const rows = await api("/evaluations/results");
  const latestInterviewSource = rows.find((row) => row.summary_json?.evaluation_type === "interview_source_smoke");
  const sourceTarget = $("#interview-source-smoke-result");
  if (sourceTarget) {
    sourceTarget.innerHTML = renderInterviewSourceSmoke(latestInterviewSource);
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
  $("#upload-profile-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const response = await fetch("/profiles/upload", { method: "POST", body: data });
    if (!response.ok) throw new Error(await response.text());
    toast("Profile created");
    form.reset();
    loadProfiles();
  });

  $("#guided-profile-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const raw = formJson(event.currentTarget);
    const projectLines = (raw.project || "").split("\n").filter(Boolean);
    const payload = {
      name: raw.name,
      email: raw.email,
      phone: raw.phone,
      target_roles: (raw.target_roles || "").split(",").map((x) => x.trim()).filter(Boolean),
      skills: (raw.skills || "").split(",").map((x) => x.trim()).filter(Boolean),
      projects: raw.project ? [{ name: projectLines[0] || "Project", description: raw.project, tech_stack: [], impact: projectLines.at(-1) || "" }] : [],
    };
    await api("/profiles/guided", { method: "POST", body: JSON.stringify(payload) });
    toast("Guided profile created");
    event.currentTarget.reset();
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
    await api("/agent/runs", { method: "POST", body: JSON.stringify(payload) });
    toast("Agent run completed");
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
    });
  });

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest("[data-import-interview-candidate]");
    if (button) prefillInterviewSourceImport(button);
  });
}

async function bootstrap() {
  bindForms();
  const page = document.body.dataset.page;
  try {
    if (page === "dashboard") {
      await loadHealth();
      await loadRecentRuns();
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
  } catch (error) {
    toast(error.message);
  }
  if (window.lucide) window.lucide.createIcons();
}

bootstrap();
