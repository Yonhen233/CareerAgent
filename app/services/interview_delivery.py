from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import InterviewPracticeItem, InterviewPrep


VALID_PRACTICE_STATUSES = {"todo", "practicing", "ready", "deferred"}

SOURCE_PERSPECTIVE_LABELS = {
    "source_backed_interview_experience": "已导入同岗面经",
    "online_experience_research": "牛客/OfferShow/小红书调研",
    "resume_project_evidence": "简历项目交付证据",
    "resume_project_stack": "简历项目技术栈",
    "jd_technical_depth": "JD 技术深挖",
    "jd_gap_drill": "JD 缺口追问",
    "general_interview": "通用面试与协作",
}


class InterviewPrepDeliveryService:
    """Render interview prep packets and track per-question practice progress."""

    def question_items(self, prep: InterviewPrep) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for group_index, group in enumerate(prep.question_sets_json or [], start=1):
            category = str(group.get("category") or f"题组 {group_index}")
            for question_index, question in enumerate(group.get("questions") or [], start=1):
                question_id = str(question.get("question_id") or f"q{group_index:02d}_{question_index:02d}")
                items.append(
                    {
                        "question_id": question_id,
                        "category": category,
                        "question": str(question.get("question") or ""),
                        "intent": str(question.get("intent") or ""),
                        "answer_points": [str(item) for item in question.get("answer_points") or []],
                        "risk_level": str(question.get("risk_level") or "low"),
                        "skills": [str(item) for item in question.get("skills") or []],
                        "source_perspective": str(question.get("source_perspective") or ""),
                        "source_label": self.source_label(str(question.get("source_perspective") or "")),
                        "evidence_refs": question.get("evidence_refs") or [],
                    }
                )
        return items

    def source_label(self, source_perspective: str) -> str:
        return SOURCE_PERSPECTIVE_LABELS.get(source_perspective, source_perspective or "未标注来源")

    def source_perspective_summary(self, prep: InterviewPrep) -> dict[str, Any]:
        items = self.question_items(prep)
        counts = Counter(item["source_perspective"] or "unknown" for item in items)
        return {
            "total_question_count": len(items),
            "counts": dict(sorted(counts.items())),
            "labels": {key: self.source_label(key) for key in sorted(counts)},
            "core_perspectives": {
                "online_experience": counts.get("online_experience_research", 0)
                + counts.get("source_backed_interview_experience", 0),
                "resume_project_stack": counts.get("resume_project_stack", 0),
                "other_interview_questions": counts.get("general_interview", 0)
                + counts.get("jd_technical_depth", 0)
                + counts.get("jd_gap_drill", 0),
            },
        }

    def list_practice_items(self, db: Session, prep: InterviewPrep) -> list[InterviewPracticeItem]:
        return (
            db.query(InterviewPracticeItem)
            .filter(InterviewPracticeItem.interview_prep_id == prep.id)
            .order_by(InterviewPracticeItem.updated_at.desc(), InterviewPracticeItem.id.desc())
            .all()
        )

    def upsert_practice_item(
        self,
        db: Session,
        prep: InterviewPrep,
        *,
        question_id: str,
        status: str,
        confidence_score: int = 0,
        notes: str | None = None,
    ) -> InterviewPracticeItem:
        question_ids = {item["question_id"] for item in self.question_items(prep)}
        if question_id not in question_ids:
            raise ValueError(f"Question id {question_id} does not belong to interview prep {prep.id}.")
        if status not in VALID_PRACTICE_STATUSES:
            raise ValueError(f"Unsupported practice status: {status}.")

        score = max(0, min(int(confidence_score), 5))
        row = (
            db.query(InterviewPracticeItem)
            .filter(
                InterviewPracticeItem.interview_prep_id == prep.id,
                InterviewPracticeItem.question_id == question_id,
            )
            .first()
        )
        if row is None:
            row = InterviewPracticeItem(
                interview_prep_id=prep.id,
                question_id=question_id,
                status=status,
                confidence_score=score,
                notes=notes,
            )
            db.add(row)
        else:
            row.status = status
            row.confidence_score = score
            row.notes = notes
        db.commit()
        db.refresh(row)
        return row

    def progress_summary(
        self,
        prep: InterviewPrep,
        practice_items: list[InterviewPracticeItem] | None = None,
    ) -> dict[str, Any]:
        questions = self.question_items(prep)
        rows = practice_items or []
        question_ids = {q["question_id"] for q in questions}
        valid_rows = [row for row in rows if row.question_id in question_ids]
        by_id = {row.question_id: row for row in valid_rows}
        status_counts = Counter(row.status for row in valid_rows)
        total = len(questions)
        ready = status_counts.get("ready", 0)
        practiced = ready + status_counts.get("practicing", 0)
        return {
            "question_count": total,
            "tracked_count": len(by_id),
            "ready_count": ready,
            "practicing_count": status_counts.get("practicing", 0),
            "deferred_count": status_counts.get("deferred", 0),
            "todo_count": max(total - practiced - status_counts.get("deferred", 0), 0),
            "ready_rate": round(ready / max(total, 1), 4),
        }

    def render_markdown(
        self,
        prep: InterviewPrep,
        *,
        practice_items: list[InterviewPracticeItem] | None = None,
    ) -> str:
        practice_by_id = {row.question_id: row for row in practice_items or []}
        summary = prep.summary_json or {}
        coverage = prep.coverage_json or {}
        perspective_summary = self.source_perspective_summary(prep)
        lines = [
            f"# {prep.title}",
            "",
            "## 基本信息",
            "",
            f"- 岗位：{summary.get('position') or prep.job.title}",
            f"- 公司：{summary.get('company') or prep.job.company or '-'}",
            f"- 匹配标签：{summary.get('fit_level') or '-'}",
            f"- 匹配分：{summary.get('overall_score', '-')}",
            f"- 题目数：{coverage.get('question_count', 0)}",
            f"- 缺口 Drill：{coverage.get('gap_drill_count', 0)}",
            f"- 来源支撑面经题：{coverage.get('source_backed_question_count', 0)}",
            "",
        ]

        if perspective_summary["counts"]:
            lines.extend(["## 问题来源分布", ""])
            for source, count in perspective_summary["counts"].items():
                label = perspective_summary["labels"].get(source, source)
                lines.append(f"- {label}：{count}")
            lines.append("")

        focus = summary.get("preparation_focus") or []
        if focus:
            lines.extend(["## 准备重点", ""])
            lines.extend(f"- {item}" for item in focus)
            lines.append("")

        matched = summary.get("matched_skills") or []
        missing = summary.get("missing_skills") or []
        if matched or missing:
            lines.extend(["## 技能覆盖", ""])
            if matched:
                lines.append("- 已匹配：" + "、".join(str(item) for item in matched))
            if missing:
                lines.append("- 待补齐：" + "、".join(str(item) for item in missing))
            lines.append("")

        for group in prep.question_sets_json or []:
            lines.extend([f"## {group.get('category') or '题组'}", ""])
            for question in group.get("questions") or []:
                question_id = str(question.get("question_id") or "")
                row = practice_by_id.get(question_id)
                status = row.status if row else "todo"
                confidence = row.confidence_score if row else 0
                lines.append(f"### [{question_id or '-'}] {question.get('question') or ''}")
                lines.append("")
                lines.append(f"- 状态：{status}")
                lines.append(f"- 信心：{confidence}/5")
                if question.get("risk_level"):
                    lines.append(f"- 风险：{question.get('risk_level')}")
                if question.get("source_perspective"):
                    lines.append(f"- 来源：{self.source_label(str(question.get('source_perspective')))}")
                if question.get("skills"):
                    lines.append("- 技能：" + "、".join(str(item) for item in question.get("skills") or []))
                if question.get("intent"):
                    lines.append(f"- 考察意图：{question.get('intent')}")
                if question.get("answer_points"):
                    lines.append("- 回答要点：")
                    lines.extend(f"  - {item}" for item in question.get("answer_points") or [])
                if row and row.notes:
                    lines.append(f"- 练习备注：{row.notes}")
                refs = question.get("evidence_refs") or []
                if refs:
                    lines.append("- 证据引用：")
                    for ref in refs:
                        preview = ref.get("preview") or ref.get("source_url") or ref.get("ref")
                        lines.append(f"  - {preview}")
                lines.append("")

        if prep.gap_drills_json:
            lines.extend(["## 缺口 Drill", ""])
            for drill in prep.gap_drills_json:
                lines.append(f"### {drill.get('skill') or '缺口'}")
                lines.append("")
                lines.append(f"- 可能追问：{drill.get('likely_question') or '-'}")
                lines.append(f"- 诚实策略：{drill.get('honest_strategy') or '-'}")
                lines.append(f"- 补齐任务：{drill.get('prep_task') or '-'}")
                lines.append("")

        if prep.research_checklist_json:
            lines.extend(["## 外部调研清单", ""])
            for item in prep.research_checklist_json:
                lines.append(f"- {item.get('site') or item.get('topic')}: {item.get('query') or '-'}")
            lines.append("")

        lines.extend(
            [
                "## 证据边界",
                "",
                str(summary.get("boundary") or "未被简历证据支持的技能只能作为待补齐项，不能包装成已交付经验。"),
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"
