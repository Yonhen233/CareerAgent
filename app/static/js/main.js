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
      <pre>${escapeHtml(row.cover_letter || "")}</pre>
    </article>
  `);
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
    });
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
  } catch (error) {
    toast(error.message);
  }
  if (window.lucide) window.lucide.createIcons();
}

bootstrap();
