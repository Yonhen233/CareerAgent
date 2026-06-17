from __future__ import annotations

import re
from html import escape
from typing import Any

from app.models.entities import Profile, ResumeVersion


class ResumeHTMLRenderer:
    """Render stored resume data into a clean, printable HTML preview."""

    def render_profile(self, profile: Profile) -> str:
        data = profile.structured_profile_json or {}
        name = data.get("name") or profile.name or "未命名简历"
        headline = data.get("headline") or profile.headline or "求职候选人"
        contact = self._join_present(
            [
                data.get("email") or profile.email,
                data.get("phone") or profile.phone,
                data.get("location"),
                data.get("availability"),
                *(data.get("portfolio_links") or []),
            ]
        )
        target_roles = data.get("target_roles") or profile.target_roles_json or []
        sections = [
            self._paragraph_section("个人总结", data.get("self_summary")),
            self._chips_section("目标岗位", target_roles),
            self._education_section(data.get("education") or []),
            self._experience_section(data.get("work_experience") or [], section_title="实习/工作经历"),
            self._projects_section(data.get("projects") or []),
            self._experience_section(data.get("campus_experience") or [], section_title="校园/实践经历"),
            self._chips_section("技能", data.get("skills") or []),
            self._list_section("证书", data.get("certifications") or []),
            self._list_section("荣誉奖项", data.get("awards") or []),
            self._chips_section("语言", data.get("languages") or []),
        ]
        body = "\n".join(section for section in sections if section)
        return self._page(
            title=f"{name} - 简历预览",
            body=f"""
            <header class="resume-header">
              <div>
                <h1>{escape(str(name))}</h1>
                <p class="headline">{escape(str(headline))}</p>
              </div>
              <div class="contact">{escape(contact)}</div>
            </header>
            {body or self._empty_state("这份简历还没有足够的结构化内容，请补充项目、经历或技能。")}
            """,
        )

    def render_resume_version(self, version: ResumeVersion) -> str:
        title = version.title or f"定制简历 #{version.id}"
        content = self.render_markdown_fragment(version.tailored_resume_markdown or "")
        aside = self._resume_version_aside(version)
        return self._page(
            title=f"{title} - HTML 预览",
            body=f"""
            <header class="resume-header">
              <div>
                <h1>{escape(title)}</h1>
                <p class="headline">面向岗位 #{escape(str(version.job_id))} 的定制简历</p>
              </div>
              <div class="contact">版本 #{escape(str(version.id))}</div>
            </header>
            <main class="resume-layout">
              <article class="resume-content">{content}</article>
              {aside}
            </main>
            """,
        )

    def render_markdown_fragment(self, markdown: str) -> str:
        lines = markdown.splitlines()
        html: list[str] = []
        list_items: list[str] = []

        def flush_list() -> None:
            if list_items:
                html.append("<ul>" + "".join(list_items) + "</ul>")
                list_items.clear()

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                flush_list()
                continue
            if line.startswith("### "):
                flush_list()
                html.append(f"<h3>{self._inline(line[4:])}</h3>")
            elif line.startswith("## "):
                flush_list()
                html.append(f"<h2>{self._inline(line[3:])}</h2>")
            elif line.startswith("# "):
                flush_list()
                html.append(f"<h1>{self._inline(line[2:])}</h1>")
            elif line.startswith(("- ", "* ")):
                list_items.append(f"<li>{self._inline(line[2:])}</li>")
            else:
                flush_list()
                html.append(f"<p>{self._inline(line)}</p>")
        flush_list()
        return "\n".join(html) or self._empty_state("暂无可预览的简历内容。")

    def _resume_version_aside(self, version: ResumeVersion) -> str:
        verification = version.verification_json or {}
        change_summary = version.change_summary_json or []
        keyword_alignment = version.keyword_alignment_json or {}
        summary_items = []
        for item in change_summary[:5]:
            if isinstance(item, dict):
                text = item.get("change") or item.get("summary") or item.get("reason") or str(item)
            else:
                text = str(item)
            summary_items.append(text)
        covered = keyword_alignment.get("covered") or keyword_alignment.get("matched") or []
        missing = keyword_alignment.get("missing") or []
        return f"""
        <aside class="resume-side">
          <section>
            <h2>检查结果</h2>
            <p><strong>{escape("通过" if verification.get("passed") else "需检查")}</strong></p>
            <p class="muted">风险：{escape(str(verification.get("risk_level") or "unknown"))}</p>
          </section>
          {self._list_section("改动摘要", summary_items)}
          {self._chips_section("已覆盖关键词", covered)}
          {self._chips_section("待补关键词", missing)}
        </aside>
        """

    def _projects_section(self, projects: list[dict[str, Any]]) -> str:
        cards = []
        for project in projects:
            name = project.get("name") or "项目经历"
            description = project.get("description") or ""
            impact = project.get("impact") or ""
            tech_stack = project.get("tech_stack") or []
            cards.append(
                f"""
                <section class="entry">
                  <h3>{escape(str(name))}</h3>
                  {f'<p>{escape(str(description))}</p>' if description else ''}
                  {f'<p class="impact">{escape(str(impact))}</p>' if impact else ''}
                  {self._chips(tech_stack)}
                </section>
                """
            )
        return self._section("项目经历", "".join(cards)) if cards else ""

    def _experience_section(self, experiences: list[dict[str, Any]], *, section_title: str = "工作与实习经历") -> str:
        cards = []
        for exp in experiences:
            title = self._join_present([exp.get("company"), exp.get("role")]) or "工作经历"
            duration = exp.get("duration") or ""
            details = exp.get("details") or ""
            cards.append(
                f"""
                <section class="entry">
                  <h3>{escape(title)}</h3>
                  {f'<p class="muted">{escape(str(duration))}</p>' if duration else ''}
                  {f'<p>{escape(str(details))}</p>' if details else ''}
                  {self._chips(exp.get("tech_stack") or [])}
                </section>
                """
            )
        return self._section(section_title, "".join(cards)) if cards else ""

    def _education_section(self, education: list[dict[str, Any]]) -> str:
        cards = []
        for edu in education:
            title = self._join_present([edu.get("school"), edu.get("degree"), edu.get("major")]) or "教育经历"
            details = self._join_present([edu.get("duration"), edu.get("details")])
            cards.append(
                f"""
                <section class="entry">
                  <h3>{escape(title)}</h3>
                  {f'<p class="muted">{escape(details)}</p>' if details else ''}
                </section>
                """
            )
        return self._section("教育经历", "".join(cards)) if cards else ""

    def _chips_section(self, title: str, values: list[Any]) -> str:
        chips = self._chips(values)
        return self._section(title, chips) if chips else ""

    def _paragraph_section(self, title: str, value: Any) -> str:
        text = str(value or "").strip()
        return self._section(title, f"<p>{escape(text)}</p>") if text else ""

    def _list_section(self, title: str, values: list[Any]) -> str:
        items = [f"<li>{escape(str(item))}</li>" for item in values if str(item).strip()]
        return self._section(title, "<ul>" + "".join(items) + "</ul>") if items else ""

    def _section(self, title: str, inner: str) -> str:
        return f"""
        <section class="resume-section">
          <h2>{escape(title)}</h2>
          {inner}
        </section>
        """

    def _chips(self, values: list[Any]) -> str:
        items = [str(item).strip() for item in values if str(item).strip()]
        if not items:
            return ""
        return '<div class="chips">' + "".join(f"<span>{escape(item)}</span>" for item in items) + "</div>"

    def _inline(self, text: str) -> str:
        escaped = escape(text)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
        return escaped

    def _join_present(self, values: list[Any]) -> str:
        return " · ".join(str(value).strip() for value in values if str(value or "").strip())

    def _empty_state(self, text: str) -> str:
        return f'<div class="empty-state">{escape(text)}</div>'

    def _page(self, *, title: str, body: str) -> str:
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172033;
      --muted: #607086;
      --line: #d9e1ea;
      --accent: #0f766e;
      --soft: #f4f7fa;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #eef2f6;
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.58;
    }}
    .page {{
      width: min(960px, calc(100% - 32px));
      margin: 28px auto;
      background: white;
      border: 1px solid var(--line);
      box-shadow: 0 22px 70px rgba(27, 39, 57, 0.12);
      padding: 44px;
    }}
    .resume-header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      border-bottom: 2px solid var(--ink);
      padding-bottom: 22px;
      margin-bottom: 28px;
    }}
    h1, h2, h3 {{ margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: 34px; line-height: 1.15; }}
    h2 {{ font-size: 16px; color: var(--accent); margin: 0 0 12px; }}
    h3 {{ font-size: 15px; margin-bottom: 6px; }}
    p {{ margin: 0 0 10px; }}
    ul {{ margin: 0; padding-left: 20px; }}
    li {{ margin: 0 0 6px; }}
    code {{ background: var(--soft); padding: 1px 5px; border-radius: 4px; }}
    .headline {{ color: var(--muted); margin-top: 8px; }}
    .contact {{ color: var(--muted); text-align: right; min-width: 180px; }}
    .resume-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 260px;
      gap: 30px;
    }}
    .resume-section {{
      padding: 18px 0;
      border-bottom: 1px solid var(--line);
    }}
    .resume-section:first-of-type {{ padding-top: 0; }}
    .entry + .entry {{ margin-top: 16px; }}
    .impact {{ color: var(--accent); font-weight: 600; }}
    .muted {{ color: var(--muted); }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 7px; margin-top: 8px; }}
    .chips span {{
      border: 1px solid var(--line);
      background: var(--soft);
      border-radius: 999px;
      padding: 4px 10px;
      color: #26364a;
      font-size: 13px;
    }}
    .resume-side {{
      border-left: 1px solid var(--line);
      padding-left: 22px;
    }}
    .resume-side section {{
      padding: 0 0 18px;
      margin-bottom: 18px;
      border-bottom: 1px solid var(--line);
    }}
    .empty-state {{
      padding: 28px;
      border: 1px dashed var(--line);
      background: var(--soft);
      color: var(--muted);
    }}
    .print-bar {{
      width: min(960px, calc(100% - 32px));
      margin: 22px auto 0;
      display: flex;
      justify-content: flex-end;
    }}
    .print-bar button {{
      border: 1px solid var(--line);
      background: white;
      color: var(--ink);
      border-radius: 8px;
      padding: 9px 14px;
      cursor: pointer;
    }}
    @media (max-width: 760px) {{
      .page {{ padding: 24px; }}
      .resume-header, .resume-layout {{ display: block; }}
      .contact {{ text-align: left; margin-top: 12px; }}
      .resume-side {{ border-left: 0; padding-left: 0; margin-top: 24px; }}
    }}
    @media print {{
      body {{ background: white; }}
      .print-bar {{ display: none; }}
      .page {{ width: 100%; margin: 0; border: 0; box-shadow: none; padding: 22mm; }}
    }}
  </style>
</head>
<body>
  <div class="print-bar"><button onclick="window.print()">打印 / 另存为 PDF</button></div>
  <div class="page">{body}</div>
</body>
</html>"""
