from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import InterviewPracticeItem, InterviewPrep
from app.services.interview_answer_framework import InterviewAnswerFrameworkService
from app.services.interview_references import InterviewReferenceService


VALID_PRACTICE_STATUSES = {"todo", "practicing", "ready", "deferred"}

SOURCE_PERSPECTIVE_LABELS = {
    "source_backed_interview_experience": "已导入同岗面经",
    "online_experience_research": "牛客/OfferShow/小红书调研",
    "resume_project_evidence": "简历项目交付证据",
    "resume_project_stack": "简历项目技术栈",
    "llm_project_implementation": "LLM 项目实现追问",
    "llm_foundation_drill": "LLM 八股与基础追问",
    "jd_technical_depth": "JD 技术深挖",
    "jd_gap_drill": "JD 缺口追问",
    "general_interview": "通用面试与协作",
}

PREPARATION_ANGLE_LABELS = {
    "same_role_interview_experience": "网上同岗位面经",
    "resume_project_tech_stack": "简历项目技术栈",
    "other_possible_interview_questions": "其他可能面试问题",
}

SOURCE_PERSPECTIVE_TO_ANGLE = {
    "source_backed_interview_experience": "same_role_interview_experience",
    "online_experience_research": "same_role_interview_experience",
    "resume_project_evidence": "resume_project_tech_stack",
    "resume_project_stack": "resume_project_tech_stack",
    "llm_project_implementation": "resume_project_tech_stack",
    "llm_foundation_drill": "other_possible_interview_questions",
    "jd_technical_depth": "other_possible_interview_questions",
    "jd_gap_drill": "other_possible_interview_questions",
    "general_interview": "other_possible_interview_questions",
}


class InterviewPrepDeliveryService:
    """Render interview prep packets and track per-question practice progress."""

    def question_items(self, prep: InterviewPrep) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for group_index, group in enumerate(self.normalized_question_sets(prep), start=1):
            category = str(group.get("category") or f"题组 {group_index}")
            for question_index, question in enumerate(group.get("questions") or [], start=1):
                question_id = str(question.get("question_id") or f"q{group_index:02d}_{question_index:02d}")
                source_perspective = str(question.get("source_perspective") or "")
                angle = str(question.get("preparation_angle") or self.preparation_angle_for_source(source_perspective))
                items.append(
                    {
                        "question_id": question_id,
                        "category": category,
                        "question": str(question.get("question") or ""),
                        "follow_ups": [str(item) for item in question.get("follow_ups") or []],
                        "intent": str(question.get("intent") or ""),
                        "answer_points": [str(item) for item in question.get("answer_points") or []],
                        "answer_framework": question.get("answer_framework") or [],
                        "answer_framework_source": str(question.get("answer_framework_source") or ""),
                        "answer_framework_source_label": str(question.get("answer_framework_source_label") or ""),
                        "question_generation_source": str(question.get("question_generation_source") or ""),
                        "question_generation_source_label": str(question.get("question_generation_source_label") or ""),
                        "risk_level": str(question.get("risk_level") or "low"),
                        "skills": [str(item) for item in question.get("skills") or []],
                        "source_perspective": source_perspective,
                        "source_label": self.source_label(source_perspective),
                        "preparation_angle": angle,
                        "preparation_angle_label": self.preparation_angle_label(angle),
                        "evidence_refs": question.get("evidence_refs") or [],
                    }
                )
        return items

    def normalized_question_sets(self, prep: InterviewPrep) -> list[dict[str, Any]]:
        return InterviewAnswerFrameworkService().normalize_question_sets(
            prep.question_sets_json,
            profile=prep.profile,
            job=prep.job,
        )

    def source_label(self, source_perspective: str) -> str:
        return SOURCE_PERSPECTIVE_LABELS.get(source_perspective, source_perspective or "未标注来源")

    def preparation_angle_for_source(self, source_perspective: str) -> str:
        return SOURCE_PERSPECTIVE_TO_ANGLE.get(source_perspective, "other_possible_interview_questions")

    def preparation_angle_label(self, angle: str) -> str:
        return PREPARATION_ANGLE_LABELS.get(angle, angle or "其他可能面试问题")

    def source_perspective_summary(self, prep: InterviewPrep) -> dict[str, Any]:
        items = self.question_items(prep)
        counts = Counter(item["source_perspective"] or "unknown" for item in items)
        angle_counts = Counter(item["preparation_angle"] or "other_possible_interview_questions" for item in items)
        return {
            "total_question_count": len(items),
            "counts": dict(sorted(counts.items())),
            "labels": {key: self.source_label(key) for key in sorted(counts)},
            "preparation_angle_counts": dict(sorted(angle_counts.items())),
            "preparation_angle_labels": {key: self.preparation_angle_label(key) for key in sorted(angle_counts)},
            "core_perspectives": {
                "online_experience": counts.get("online_experience_research", 0)
                + counts.get("source_backed_interview_experience", 0),
                "resume_project_stack": counts.get("resume_project_stack", 0)
                + counts.get("resume_project_evidence", 0)
                + counts.get("llm_project_implementation", 0),
                "other_interview_questions": counts.get("general_interview", 0)
                + counts.get("jd_technical_depth", 0)
                + counts.get("jd_gap_drill", 0)
                + counts.get("llm_foundation_drill", 0),
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
            if notes is not None:
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

        reference_links = InterviewReferenceService.normalize_links(summary.get("interview_reference_links") or [])
        if reference_links:
            lines.extend(["## 面经参考链接", ""])
            for item in reference_links:
                title = item.get("title") or item.get("site") or "面经参考"
                url = item.get("url") or item.get("query") or "-"
                lines.append(f"- {title}: {url}")
                if item.get("note"):
                    lines.append(f"  - 边界：{item.get('note')}")
            lines.append("")

        preparation_angles = summary.get("preparation_angles") or self._preparation_angles_from_summary(perspective_summary)
        if preparation_angles:
            lines.extend(["## 准备角度", ""])
            for angle in preparation_angles:
                lines.append(f"### {angle.get('label') or self.preparation_angle_label(str(angle.get('angle') or ''))}")
                lines.append("")
                lines.append(f"- 题目数：{angle.get('question_count', 0)}")
                source_inputs = angle.get("source_inputs") or []
                if source_inputs:
                    lines.append("- 输入来源：" + "；".join(str(item) for item in source_inputs if str(item).strip()))
                focus = angle.get("focus") or []
                if focus:
                    lines.append("- 准备重点：" + "；".join(str(item) for item in focus if str(item).strip()))
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

        for group in self.normalized_question_sets(prep):
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
                angle = str(
                    question.get("preparation_angle")
                    or self.preparation_angle_for_source(str(question.get("source_perspective") or ""))
                )
                lines.append(f"- 准备角度：{self.preparation_angle_label(angle)}")
                if question.get("skills"):
                    lines.append("- 技能：" + "、".join(str(item) for item in question.get("skills") or []))
                if question.get("intent"):
                    lines.append(f"- 考察意图：{question.get('intent')}")
                if question.get("follow_ups"):
                    lines.append("- 连续追问：")
                    lines.extend(f"  - {item}" for item in question.get("follow_ups") or [])
                if question.get("answer_framework_source_label"):
                    lines.append(f"- 回答框架来源：{question.get('answer_framework_source_label')}")
                if question.get("answer_framework"):
                    lines.append("- 回答框架：")
                    lines.extend(
                        f"  - {item.get('section')}：{item.get('guidance')}"
                        for item in question.get("answer_framework") or []
                    )
                if row and row.notes:
                    lines.append(f"- 练习备注：{row.notes}")
                refs = question.get("evidence_refs") or []
                if refs:
                    lines.append("- 证据引用：")
                    for ref in refs:
                        preview = ref.get("preview") or ref.get("source_label") or "证据已记录"
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

    def _preparation_angles_from_summary(self, perspective_summary: dict[str, Any]) -> list[dict[str, Any]]:
        counts = perspective_summary.get("preparation_angle_counts") or {}
        return [
            {
                "angle": angle,
                "label": self.preparation_angle_label(angle),
                "question_count": counts.get(angle, 0),
            }
            for angle in PREPARATION_ANGLE_LABELS
            if counts.get(angle, 0)
        ]
