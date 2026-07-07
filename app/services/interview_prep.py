from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMClient, LLMConfigurationError, extract_json_object, format_exception
from app.models.entities import InterviewPrep, Job, MatchResult, Profile
from app.services.interview_experience import InterviewExperienceService
from app.services.matcher import MatcherService, normalize_skill


INTERVIEW_PREP_ANGLE_LABELS = {
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


class InterviewPrepService:
    """Build an evidence-backed interview preparation packet from Profile, JD, and match trace."""

    def __init__(
        self,
        matcher: MatcherService | None = None,
        experience_service: InterviewExperienceService | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.settings = get_settings()
        self.matcher = matcher or MatcherService()
        self.experience_service = experience_service or InterviewExperienceService()
        self.llm = llm or LLMClient()

    def create_interview_prep(
        self,
        db: Session,
        *,
        profile: Profile,
        job: Job,
        match_result: MatchResult | None = None,
        experience_ids: list[int] | None = None,
        llm_question_sets: list[dict[str, Any]] | None = None,
        generation_mode: str = "structured_rules_v3_preparation_angles",
    ) -> InterviewPrep:
        match = match_result or self.matcher.create_match_result(db, profile, job)
        evidence = self._evidence(match)
        experience_rows = self.experience_service.find_relevant_for_job(
            db,
            job=job,
            experience_ids=experience_ids,
        )
        experience_evidence = [
            self.experience_service.to_evidence(row, score)
            for row, score in experience_rows
        ]
        required = self._skills(job, "required_skills")
        preferred = self._skills(job, "preferred_skills")
        keywords = self._skills(job, "keywords")
        matched = [str(item) for item in match.matched_skills_json or []]
        missing = [str(item) for item in match.missing_skills_json or []]

        question_sets = [
            self._source_backed_experience_questions(job, experience_evidence, required, missing),
            self._online_experience_questions(job, required, missing),
            self._project_deep_dive_questions(profile, job, evidence, matched),
            self._project_stack_questions(profile, job, evidence),
            *(llm_question_sets or []),
            self._technical_questions(job, evidence, matched, required, preferred, keywords),
            self._gap_questions(job, missing),
            self._collaboration_questions(job),
            self._general_interview_questions(profile, job),
        ]
        question_sets = [item for item in question_sets if item["questions"]]
        question_sets = self._dedupe_question_sets(question_sets)
        self._attach_question_metadata(question_sets, missing=missing)
        gap_drills = self._gap_drills(job, missing)
        research_checklist = self._research_checklist(job, required, missing)
        question_quality = self._question_quality_judge(
            profile=profile,
            job=job,
            question_sets=question_sets,
            required=required,
            preferred=preferred,
            keywords=keywords,
            missing=missing,
            evidence=evidence,
        )
        coverage = self._coverage(
            required=required,
            matched=matched,
            missing=missing,
            question_sets=question_sets,
            gap_drills=gap_drills,
            evidence=evidence,
            experience_evidence=experience_evidence,
            question_quality=question_quality,
        )
        source_evidence = [*experience_evidence, *evidence]
        interview_reference_links = self._interview_reference_links(
            job=job,
            experience_evidence=experience_evidence,
            research_checklist=research_checklist,
        )
        preparation_angles = self._preparation_angles(
            profile=profile,
            job=job,
            question_sets=question_sets,
            research_checklist=research_checklist,
            coverage=coverage,
            experience_evidence=experience_evidence,
            required=required,
            missing=missing,
        )
        summary = {
            "position": job.title,
            "company": job.company,
            "overall_score": match.overall_score,
            "fit_level": self._fit_level(match.overall_score),
            "matched_skills": matched[:10],
            "missing_skills": missing[:10],
            "interview_experience_source_count": len(experience_evidence),
            "interview_experience_sites": sorted(
                {str(item.get("source_site")) for item in experience_evidence if item.get("source_site")}
            ),
            "preparation_focus": self._preparation_focus(match, missing, evidence),
            "boundary": "缺少证据的技能只能作为待补强或诚实披露，不能包装成已交付经验。",
            "source_perspectives": ["同岗位面经/面经调研", "简历项目技术栈深挖", "其他可能面试问题"],
            "preparation_angles": preparation_angles,
            "interview_reference_links": interview_reference_links,
            "question_quality": question_quality,
            "llm_question_generation": {
                "enabled": bool(llm_question_sets),
                "question_set_count": len(llm_question_sets or []),
                "mode": "llm_augmented" if llm_question_sets else "structured_rules",
            },
        }
        prep = InterviewPrep(
            profile_id=profile.id,
            job_id=job.id,
            match_result_id=match.id,
            title=f"{job.title} 面试准备包",
            summary_json=summary,
            question_sets_json=question_sets,
            gap_drills_json=gap_drills,
            research_checklist_json=research_checklist,
            source_evidence_json=source_evidence,
            coverage_json=coverage,
            generation_mode=generation_mode,
        )
        db.add(prep)
        db.commit()
        db.refresh(prep)
        return prep

    async def create_interview_prep_with_llm(
        self,
        db: Session,
        *,
        profile: Profile,
        job: Job,
        match_result: MatchResult | None = None,
        experience_ids: list[int] | None = None,
    ) -> InterviewPrep:
        match = match_result or self.matcher.create_match_result(db, profile, job)
        evidence = self._evidence(match)
        required = self._skills(job, "required_skills")
        preferred = self._skills(job, "preferred_skills")
        keywords = self._skills(job, "keywords")
        matched = [str(item) for item in match.matched_skills_json or []]
        missing = [str(item) for item in match.missing_skills_json or []]
        llm_question_sets = await self._llm_question_sets(
            db=db,
            profile=profile,
            job=job,
            evidence=evidence,
            matched=matched,
            missing=missing,
            required=required,
            preferred=preferred,
            keywords=keywords,
        )
        return self.create_interview_prep(
            db,
            profile=profile,
            job=job,
            match_result=match,
            experience_ids=experience_ids,
            llm_question_sets=llm_question_sets,
            generation_mode="llm_augmented_v1_jd_project_questions",
        )

    def _attach_question_metadata(self, question_sets: list[dict[str, Any]], *, missing: list[str] | None = None) -> None:
        missing_norm = {normalize_skill(item) for item in missing or [] if normalize_skill(item)}
        for group_index, group in enumerate(question_sets, start=1):
            for question_index, question in enumerate(group.get("questions") or [], start=1):
                question.setdefault("question_id", f"q{group_index:02d}_{question_index:02d}")
                angle = self._angle_for_source(str(question.get("source_perspective") or ""))
                question.setdefault("preparation_angle", angle)
                question.setdefault("preparation_angle_label", INTERVIEW_PREP_ANGLE_LABELS[angle])
                follow_ups = [str(item).strip() for item in question.get("follow_ups") or [] if str(item).strip()]
                question["follow_ups"] = follow_ups[:3] or self._default_follow_ups(question, missing_norm=missing_norm)

    def _default_follow_ups(self, question: dict[str, Any], *, missing_norm: set[str] | None = None) -> list[str]:
        source = str(question.get("source_perspective") or "")
        skills = [str(item).strip() for item in question.get("skills") or [] if str(item).strip()]
        skill_text = "、".join(skills[:2]) if skills else "这个能力点"
        question_missing_norm = {normalize_skill(item) for item in skills if normalize_skill(item)}
        touches_missing_skill = bool((missing_norm or set()) & question_missing_norm)
        if touches_missing_skill or source in {"jd_gap_drill", "llm_foundation_drill"} or question.get("risk_level") == "high":
            return [
                f"如果没有真实交付过 {skill_text}，你会如何诚实说明边界？",
                "你准备用什么最小验证任务补齐这个短板？",
            ]
        if source in {"resume_project_evidence", "resume_project_stack", "llm_project_implementation"}:
            return [
                "这个项目里你的个人贡献边界是什么？",
                "如果该设计失败，你会看哪些日志、指标或样例来定位？",
            ]
        if source in {"online_experience_research", "source_backed_interview_experience"}:
            return [
                "这个面经线索和当前 JD 的哪些要求最相关？",
                "你会用简历里的哪个项目或经历作为回答主线？",
            ]
        return [
            "这个回答如何回到当前 JD 的具体职责？",
                "你会用哪个项目、指标或失败案例支撑这个回答？",
        ]

    def _dedupe_question_sets(self, question_sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        deduped_sets: list[dict[str, Any]] = []
        for group in question_sets:
            unique_questions = []
            for question in group.get("questions") or []:
                key = self._normalize_question_text(str(question.get("question") or ""))
                if not key or key in seen:
                    continue
                seen.add(key)
                unique_questions.append(question)
            if unique_questions:
                new_group = dict(group)
                new_group["questions"] = unique_questions
                deduped_sets.append(new_group)
        return deduped_sets

    def _source_backed_experience_questions(
        self,
        job: Job,
        experience_evidence: list[dict[str, Any]],
        required: list[str],
        missing: list[str],
    ) -> dict[str, Any]:
        questions = []
        focus = self._unique([*required[:4], *missing[:3]])
        focus_text = "、".join(focus[:4]) if focus else job.title
        for source in experience_evidence[:5]:
            source_questions = source.get("questions") or []
            credibility = source.get("credibility") or {}
            risk_level = "low" if float(credibility.get("score") or 0) >= 0.65 else "medium"
            for item in source_questions[:3]:
                raw_question = str(item.get("question") or "").strip()
                if not raw_question:
                    continue
                topics = [str(topic) for topic in (item.get("topics") or source.get("topics") or []) if str(topic).strip()]
                questions.append(
                    {
                        "question": (
                            f"导入面经（{source.get('source_site')}）提到：{raw_question} "
                            f"请结合 {job.title} 和你的简历证据准备回答。"
                        ),
                        "intent": "把真实同岗面经问题映射到当前 JD、简历项目证据和能力缺口，避免只背通用题库。",
                        "answer_points": [
                            f"先标注来源：{source.get('source_site')} / {source.get('title') or source.get('role_keyword') or job.title}。",
                            f"围绕 {focus_text} 说明可引用的项目、指标或缺口边界。",
                            "如果该题涉及未交付技能，只能说明相邻经验和补齐计划，不能包装成生产经验。",
                        ],
                        "evidence_refs": [
                            {
                                "ref": f"interview_experience:{source.get('source_id')}",
                                "source_site": source.get("source_site"),
                                "source_url": source.get("source_url"),
                                "credibility_score": credibility.get("score"),
                                "preview": item.get("source_quote") or source.get("text_preview"),
                            }
                        ],
                        "risk_level": risk_level,
                        "skills": topics[:5] or focus[:3],
                        "source_perspective": "source_backed_interview_experience",
                    }
                )
        return {"category": "已导入面经追问", "questions": questions[:10]}

    def _online_experience_questions(
        self,
        job: Job,
        required: list[str],
        missing: list[str],
    ) -> dict[str, Any]:
        focus = self._unique([*required[:5], *missing[:3]])
        focus_text = "、".join(focus[:4]) if focus else "岗位核心技术"
        questions = [
            {
                "question": f"查看面试包附带的同岗位面经参考链接和标题后，哪些内容需要回到 {job.title} 的 JD 和简历项目里重点准备？",
                "intent": "把面经平台当作参考入口，只沉淀标题、链接和待核验主题，不把难抓取正文当作核心依赖。",
                "answer_points": [
                    f"先围绕 {focus_text} 标记和岗位重合的技术词。",
                    "只把链接标题当作调研线索，真正回答仍回到 JD、简历项目证据和缺口 drill。",
                    "如果平台需要登录或无法获取正文，停止抓取，把可访问链接和标题附在面试包末尾。",
                ],
                "evidence_refs": [],
                "risk_level": "medium",
                "skills": focus[:4],
                "source_perspective": "online_experience_research",
            },
            {
                "question": f"如果参考链接标题里反复出现 {focus_text}，你准备用哪个简历项目作为回答主线？",
                "intent": "让面经线索服务于项目准备，而不是继续扩大外部抓取复杂度。",
                "answer_points": [
                    "选择最接近 JD 的项目作为主线。",
                    "每个技术点准备一个原理解释、一个工程取舍和一个失败/限制。",
                ],
                "evidence_refs": [],
                "risk_level": "medium",
                "skills": focus[:4],
                "source_perspective": "online_experience_research",
            },
        ]
        return {"category": "同岗位面经与高频追问", "questions": questions}

    async def _llm_question_sets(
        self,
        *,
        db: Session,
        profile: Profile,
        job: Job,
        evidence: list[dict[str, Any]],
        matched: list[str],
        missing: list[str],
        required: list[str],
        preferred: list[str],
        keywords: list[str],
    ) -> list[dict[str, Any]]:
        if not self.llm.available:
            if not self.settings.llm_fallback_enabled:
                raise LLMConfigurationError(
                    "LLM is required for interview question generation. "
                    "Set LLM_FALLBACK_ENABLED=true only for deterministic tests."
                )
            return self._heuristic_llm_question_sets(profile, job, evidence, matched, missing, required, preferred)

        system_prompt = (
            "你是一位中文技术面试官，负责根据岗位 JD、候选人简历项目和 RAG 证据生成面试问题。"
            "只输出 JSON，不要输出 Markdown。不要编造候选人没有的经历；缺口必须设计成诚实披露追问。"
        )
        user_prompt = self._llm_question_prompt(
            profile=profile,
            job=job,
            evidence=evidence,
            matched=matched,
            missing=missing,
            required=required,
            preferred=preferred,
            keywords=keywords,
        )
        raw_text = await self._generate_question_text_with_retry(
            db=db,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        try:
            payload = extract_json_object(raw_text)
        except Exception as exc:
            try:
                payload = await self._repair_llm_question_json(
                    db=db,
                    raw_text=raw_text,
                    parse_error=format_exception(exc),
                )
            except Exception:
                payload = self._recover_partial_question_payload(raw_text)
        return self._normalize_llm_question_sets(payload, required=required, missing=missing)

    async def _generate_question_text_with_retry(
        self,
        *,
        db: Session,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        last_exc: Exception | None = None
        for attempt in range(2):
            trace_name = "interview_prep.generate_interviewer_questions"
            if attempt:
                trace_name = f"{trace_name}.retry_{attempt}"
            try:
                return await self.llm.generate_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.35,
                    max_tokens=1200,
                    response_format={"type": "json_object"},
                    db=db,
                    trace_name=trace_name,
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                message = format_exception(exc)
                if attempt >= 1 or not self._is_transient_llm_error(message):
                    raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("LLM question generation did not return text.")

    def _is_transient_llm_error(self, message: str) -> bool:
        transient_terms = [
            "ReadTimeout",
            "ConnectTimeout",
            "RemoteProtocolError",
            "LLM returned empty content",
            "temporarily unavailable",
            "connection reset",
        ]
        return any(term.lower() in message.lower() for term in transient_terms)

    def _llm_question_prompt(
        self,
        *,
        profile: Profile,
        job: Job,
        evidence: list[dict[str, Any]],
        matched: list[str],
        missing: list[str],
        required: list[str],
        preferred: list[str],
        keywords: list[str],
    ) -> str:
        projects = []
        for project in (profile.structured_profile_json or {}).get("projects", []) or []:
            if not isinstance(project, dict):
                continue
            projects.append(
                {
                    "name": project.get("name"),
                    "description": self._short_text(project.get("description"), 220),
                    "tech_stack": project.get("tech_stack") or [],
                    "impact": self._short_text(project.get("impact"), 160),
                }
            )
        context = {
            "job": {
                "title": job.title,
                "company": job.company,
                "required_skills": required,
                "preferred_skills": preferred,
                "keywords": keywords[:12],
                "responsibilities": (job.structured_jd_json or {}).get("responsibilities", [])[:6],
                "raw_jd_preview": self._short_text(job.raw_jd_text, 520),
            },
            "candidate": {
                "name": profile.name,
                "headline": profile.headline,
                "target_roles": profile.target_roles_json or [],
                "skills": (profile.structured_profile_json or {}).get("skills", [])[:20],
                "projects": projects[:5],
            },
            "match": {
                "matched_skills": matched[:12],
                "missing_skills": missing[:10],
            },
            "rag_evidence": evidence[:6],
        }
        return (
            "请基于以下上下文输出紧凑 JSON，只生成问题，不写回答解析。\n"
            "Schema: {\"project_questions\":[{\"question\":string,\"follow_ups\":[string,string],"
            "\"skills\":[string],\"risk_level\":\"low|medium|high\"}],"
            "\"foundation_questions\":[{\"question\":string,\"follow_ups\":[string,string],"
            "\"skills\":[string],\"risk_level\":\"low|medium|high\"}]}\n"
            "project_questions 只生成 2 题，围绕简历项目架构、数据流、日志指标、失败边界和本人贡献。\n"
            "foundation_questions 只生成 2 题，围绕 JD 技能八股、底层原理、工程取舍和缺口诚实披露。\n"
            "每个字符串少于 60 个中文字符。不要 Markdown，不要额外字段。"
            "如果某技能在 missing_skills 里，问题必须要求候选人诚实说明边界和补齐计划，不能假设已经做过。\n\n"
            f"上下文：\n{json.dumps(context, ensure_ascii=False, indent=2)}"
        )

    async def _repair_llm_question_json(
        self,
        *,
        db: Session,
        raw_text: str,
        parse_error: str,
    ) -> dict[str, Any]:
        system_prompt = "你只负责修复 JSON。只输出一个合法 JSON object，不要 Markdown，不要解释。"
        user_prompt = (
            "下面是一次面试题生成的模型输出，JSON 可能被截断或有语法错误。"
            "请修复成 schema："
            '{"project_questions":[{"question":string,"follow_ups":[string,string],'
            '"skills":[string],"risk_level":"low|medium|high"}],'
            '"foundation_questions":[{"question":string,"follow_ups":[string,string],'
            '"skills":[string],"risk_level":"low|medium|high"}]}\n'
            "约束：每组最多 2 题；每个字符串少于 60 个中文字符。\n"
            f"解析错误：{parse_error}\n"
            f"原始输出：\n{raw_text[:5000]}"
        )
        repaired_text = await self.llm.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0,
            max_tokens=900,
            response_format={"type": "json_object"},
            db=db,
            trace_name="interview_prep.repair_question_json",
        )
        return extract_json_object(repaired_text)

    def _normalize_llm_question_sets(
        self,
        payload: dict[str, Any],
        *,
        required: list[str],
        missing: list[str],
    ) -> list[dict[str, Any]]:
        compact_sets = self._normalize_compact_question_payload(payload, required=required, missing=missing)
        if compact_sets:
            return compact_sets

        raw_sets = payload.get("question_sets")
        if not isinstance(raw_sets, list):
            raise ValueError("LLM interview question payload must contain question_sets list.")
        normalized_sets: list[dict[str, Any]] = []
        allowed_sources = {"llm_project_implementation", "llm_foundation_drill"}
        for raw_group in raw_sets[:3]:
            if not isinstance(raw_group, dict):
                continue
            category = str(raw_group.get("category") or "").strip()
            questions = []
            for raw_question in (raw_group.get("questions") or [])[:8]:
                if not isinstance(raw_question, dict):
                    continue
                question_text = str(raw_question.get("question") or "").strip()
                if not question_text:
                    continue
                source_perspective = str(raw_question.get("source_perspective") or "").strip()
                if source_perspective not in allowed_sources:
                    source_perspective = (
                        "llm_project_implementation"
                        if "项目" in category or "实现" in category
                        else "llm_foundation_drill"
                    )
                skills = [str(item).strip() for item in raw_question.get("skills") or [] if str(item).strip()]
                risk_level = str(raw_question.get("risk_level") or "medium").lower()
                if risk_level not in {"low", "medium", "high"}:
                    risk_level = "medium"
                if any(normalize_skill(skill) in {normalize_skill(item) for item in missing} for skill in skills):
                    risk_level = "high"
                questions.append(
                    {
                        "question": question_text,
                        "follow_ups": [
                            str(item).strip()
                            for item in raw_question.get("follow_ups") or []
                            if str(item).strip()
                        ][:4],
                        "intent": str(raw_question.get("intent") or "模拟真实面试官围绕 JD 和简历证据追问。"),
                        "answer_points": [
                            str(item).strip()
                            for item in raw_question.get("answer_points") or []
                            if str(item).strip()
                        ][:5]
                        or self._technical_answer_points(skills[0] if skills else (required[0] if required else "岗位核心能力"), None),
                        "evidence_refs": [],
                        "risk_level": risk_level,
                        "skills": skills[:6],
                        "source_perspective": source_perspective,
                    }
                )
            if questions:
                normalized_sets.append(
                    {
                        "category": category or self._llm_category_for_source(questions[0]["source_perspective"]),
                        "questions": questions,
                    }
                )
        if not normalized_sets:
            raise ValueError("LLM interview question payload did not contain usable questions.")
        return normalized_sets

    def _normalize_compact_question_payload(
        self,
        payload: dict[str, Any],
        *,
        required: list[str],
        missing: list[str],
    ) -> list[dict[str, Any]]:
        groups = [
            ("project_questions", "LLM 项目实现追问", "llm_project_implementation"),
            ("foundation_questions", "LLM 八股与基础追问", "llm_foundation_drill"),
        ]
        normalized_sets: list[dict[str, Any]] = []
        for key, category, source_perspective in groups:
            questions = []
            raw_questions = payload.get(key)
            if not isinstance(raw_questions, list):
                continue
            for raw_question in raw_questions[:3]:
                if not isinstance(raw_question, dict):
                    continue
                question_text = str(raw_question.get("question") or "").strip()
                if not question_text:
                    continue
                skills = [str(item).strip() for item in raw_question.get("skills") or [] if str(item).strip()]
                skill = skills[0] if skills else (required[0] if required else "岗位核心能力")
                risk_level = str(raw_question.get("risk_level") or "medium").lower()
                if risk_level not in {"low", "medium", "high"}:
                    risk_level = "medium"
                if any(normalize_skill(item) in {normalize_skill(miss) for miss in missing} for item in skills):
                    risk_level = "high"
                questions.append(
                    {
                        "question": question_text,
                        "follow_ups": [
                            str(item).strip()
                            for item in raw_question.get("follow_ups") or []
                            if str(item).strip()
                        ][:3],
                        "intent": (
                            "模拟面试官围绕简历项目实现做连续追问。"
                            if source_perspective == "llm_project_implementation"
                            else "模拟面试官围绕 JD 技能八股、工程取舍和缺口边界追问。"
                        ),
                        "answer_points": self._technical_answer_points(skill, None),
                        "evidence_refs": [],
                        "risk_level": risk_level,
                        "skills": skills[:6],
                        "source_perspective": source_perspective,
                    }
                )
            if questions:
                normalized_sets.append({"category": category, "questions": questions})
        return normalized_sets

    def _recover_partial_question_payload(self, raw_text: str) -> dict[str, Any]:
        decoder = json.JSONDecoder()
        project_questions: list[dict[str, Any]] = []
        foundation_questions: list[dict[str, Any]] = []
        for index, char in enumerate(raw_text):
            if char != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(raw_text[index:])
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or not obj.get("question"):
                continue
            haystack = json.dumps(obj, ensure_ascii=False)
            if "foundation" in haystack or "八股" in haystack or "基础" in haystack:
                foundation_questions.append(obj)
            elif "project" in haystack or "项目" in haystack or "实现" in haystack:
                project_questions.append(obj)
        return {
            "project_questions": project_questions[:2],
            "foundation_questions": foundation_questions[:2],
        }

    def _heuristic_llm_question_sets(
        self,
        profile: Profile,
        job: Job,
        evidence: list[dict[str, Any]],
        matched: list[str],
        missing: list[str],
        required: list[str],
        preferred: list[str],
    ) -> list[dict[str, Any]]:
        projects = [
            item
            for item in (profile.structured_profile_json or {}).get("projects", []) or []
            if isinstance(item, dict)
        ]
        project = projects[0] if projects else {}
        project_name = str(project.get("name") or "简历项目")
        stack = self._unique(
            [
                *(str(item) for item in project.get("tech_stack", []) if str(item).strip()),
                *matched[:4],
                *required[:4],
            ]
        )
        foundation_skills = self._unique([*required[:5], *preferred[:3], *missing[:3]])[:6]
        project_questions = []
        for skill in stack[:5] or ["项目架构"]:
            evidence_item = self._find_evidence_for_skill(evidence, skill)
            project_questions.append(
                {
                    "question": f"在 {project_name} 里，{skill} 具体处在架构的哪一层？请从输入、处理、输出和失败边界讲清楚。",
                    "follow_ups": [
                        f"如果 {skill} 这一层延迟或错误率升高，你会先看哪些日志或指标？",
                        "这个设计有没有替代方案？当时为什么没有选替代方案？",
                        "这部分哪些是你本人完成的，哪些依赖团队或开源组件？",
                    ],
                    "intent": "追问项目实现细节，验证候选人是否真的理解简历里的技术栈。",
                    "answer_points": self._technical_answer_points(skill, evidence_item),
                    "evidence_refs": [self._evidence_ref(evidence_item, 1)] if evidence_item else [],
                    "risk_level": "low" if evidence_item else "medium",
                    "skills": [skill],
                    "source_perspective": "llm_project_implementation",
                }
            )
        foundation_questions = []
        for skill in foundation_skills[:6] or ["岗位核心能力"]:
            is_missing = normalize_skill(skill) in {normalize_skill(item) for item in missing}
            foundation_questions.append(
                {
                    "question": f"面试官如果考 {skill} 的基础原理和工程取舍，你会如何结合 {job.title} 的 JD 场景回答？",
                    "follow_ups": [
                        f"{skill} 的核心输入、输出和常见失败模式是什么？",
                        "如果让你用 1 天做一个最小验证 demo，你会怎么拆？",
                        "如果你没有真实生产经验，哪些话可以说，哪些不能说？",
                    ],
                    "intent": "覆盖八股、底层原理和岗位场景化追问。",
                    "answer_points": [
                        f"先解释 {skill} 的概念、适用边界和在 JD 中的用途。",
                        "结合简历项目讲相邻经验；没有证据时明确说成待补齐。",
                        "给出可验证指标、实验方法或最小 demo。",
                    ],
                    "evidence_refs": [],
                    "risk_level": "high" if is_missing else "medium",
                    "skills": [skill],
                    "source_perspective": "llm_foundation_drill",
                }
            )
        return [
            {"category": "LLM 项目实现追问", "questions": project_questions[:5]},
            {"category": "LLM 八股与基础追问", "questions": foundation_questions[:6]},
        ]

    def _project_deep_dive_questions(
        self,
        profile: Profile,
        job: Job,
        evidence: list[dict[str, Any]],
        matched: list[str],
    ) -> dict[str, Any]:
        top_evidence = [item for item in evidence if item.get("polarity") != "negative"][:4]
        questions = []
        for index, item in enumerate(top_evidence, start=1):
            skills = self._evidence_skills(item, matched)
            skill_text = "、".join(skills[:3]) if skills else "岗位相关能力"
            questions.append(
                {
                    "question": f"请结合一个项目，说明你如何体现 {skill_text}，以及这个项目为什么适合 {job.title}。",
                    "intent": "验证候选人是否真的交付过相关项目，而不是只会列技术名词。",
                    "answer_points": [
                        f"先用 1 句话交代项目背景：{self._short_text(item.get('text'), 70)}",
                        "说明你本人负责的模块、关键技术选择和最终可验证结果。",
                        "主动说清边界：没有做过的工具或生产规模不要硬认。",
                    ],
                    "evidence_refs": [self._evidence_ref(item, index)],
                    "risk_level": self._question_risk(item),
                    "skills": skills,
                    "source_perspective": "resume_project_evidence",
                }
            )
        if not questions and profile.raw_resume_text:
            questions.append(
                {
                    "question": f"请从简历中挑一个最接近 {job.title} 的项目，按背景、行动、结果讲 90 秒。",
                    "intent": "缺少可检索证据时，先建立可追问的项目主线。",
                    "answer_points": ["选择真实交付项目。", "避免把目标岗位意向当作项目经验。"],
                    "evidence_refs": [],
                    "risk_level": "medium",
                    "skills": matched[:3],
                    "source_perspective": "resume_project_evidence",
                }
            )
        return {"category": "项目深挖", "questions": questions}

    def _project_stack_questions(
        self,
        profile: Profile,
        job: Job,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        stack = self._profile_tech_stack(profile)[:8]
        questions = []
        for skill in stack:
            evidence_item = self._find_evidence_for_skill(evidence, skill)
            questions.append(
                {
                    "question": f"你的简历项目里用了 {skill}，请说明它在项目架构中的位置、为什么选它，以及如果不用它有什么替代方案。",
                    "intent": "从候选人项目技术栈出发做深挖，验证是否真的理解自己写在简历里的技术。",
                    "answer_points": self._technical_answer_points(skill, evidence_item),
                    "evidence_refs": [self._evidence_ref(evidence_item, 1)] if evidence_item else [],
                    "risk_level": "low" if evidence_item else "medium",
                    "skills": [skill],
                    "source_perspective": "resume_project_stack",
                }
            )
        return {"category": "简历项目技术栈追问", "questions": questions[:6]}

    def _technical_questions(
        self,
        job: Job,
        evidence: list[dict[str, Any]],
        matched: list[str],
        required: list[str],
        preferred: list[str],
        keywords: list[str],
    ) -> dict[str, Any]:
        candidate_skills = self._unique([*matched, *required[:4], *preferred[:3], *keywords[:3]])[:8]
        questions = []
        for skill in candidate_skills:
            evidence_item = self._find_evidence_for_skill(evidence, skill)
            refs = [self._evidence_ref(evidence_item, 1)] if evidence_item else []
            questions.append(
                {
                    "question": f"{skill} 在这个岗位里可能怎么用？请结合 JD 设计一个从输入到输出的实现方案。",
                    "intent": "检查技术理解、工程拆解和与 JD 场景的贴合度。",
                    "answer_points": self._technical_answer_points(skill, evidence_item),
                    "evidence_refs": refs,
                    "risk_level": "low" if evidence_item and skill in matched else "medium",
                    "skills": [skill],
                    "source_perspective": "jd_technical_depth",
                }
            )
        return {"category": "技术深挖", "questions": questions}

    def _gap_questions(self, job: Job, missing: list[str]) -> dict[str, Any]:
        questions = [
            {
                "question": f"JD 提到 {skill}，如果面试官追问你没有相关交付经验，你会如何诚实说明并给出补齐计划？",
                "intent": "验证候选人能否处理能力缺口，不编造经历。",
                "answer_points": [
                    f"明确说明当前 {skill} 是缺口或仅有相邻经验。",
                    "迁移已有项目中的相邻能力，但不要说成已经生产落地。",
                    "给出 1 个可在 3-7 天内完成的小验证任务。",
                ],
                "evidence_refs": [],
                "risk_level": "high",
                "skills": [skill],
                "source_perspective": "jd_gap_drill",
            }
            for skill in missing[:6]
        ]
        if not questions:
            questions.append(
                {
                    "question": f"如果 {job.title} 的业务场景和你做过的项目不同，你会如何快速补齐领域知识？",
                    "intent": "强匹配候选人也需要证明学习路径和业务理解。",
                    "answer_points": ["先复述业务目标。", "列出数据、系统、评测三类待确认问题。"],
                    "evidence_refs": [],
                    "risk_level": "low",
                    "skills": [],
                    "source_perspective": "jd_gap_drill",
                }
            )
        return {"category": "缺口追问", "questions": questions}

    def _collaboration_questions(self, job: Job) -> dict[str, Any]:
        responsibilities = [
            str(item).strip()
            for item in (job.structured_jd_json or {}).get("responsibilities", [])
            if str(item).strip()
        ]
        seeds = responsibilities[:3] or [job.raw_jd_text[:180] or job.title]
        questions = [
            {
                "question": f"如果入职后要推进这项工作：{self._short_text(seed, 60)}，你会如何拆任务、同步风险并验证效果？",
                "intent": "检查工程协作、问题拆解和结果意识。",
                "answer_points": [
                    "把目标拆成数据/接口/模型或评测/上线验证几段。",
                    "说明需要向导师或团队确认的约束。",
                    "给出可观测指标，例如准确率、召回、延迟、通过率或用户反馈。",
                ],
                "evidence_refs": [],
                "risk_level": "low",
                "skills": [],
                "source_perspective": "general_interview",
            }
            for seed in seeds
        ]
        return {"category": "工程协作与落地", "questions": questions}

    def _general_interview_questions(self, profile: Profile, job: Job) -> dict[str, Any]:
        target_role = ", ".join(profile.target_roles_json or []) or job.title
        return {
            "category": "通用面试与行为问题",
            "questions": [
                {
                    "question": f"为什么你想做 {target_role}，以及为什么这个岗位适合你当前阶段？",
                    "intent": "检查求职动机、岗位理解和表达稳定性。",
                    "answer_points": [
                        "用 1 个真实项目连接岗位方向。",
                        "说清希望在实习中补齐的能力，而不是只说感兴趣。",
                    ],
                    "evidence_refs": [],
                    "risk_level": "low",
                    "skills": [],
                    "source_perspective": "general_interview",
                },
                {
                    "question": "讲一次项目中遇到的失败、返工或指标不达预期，你怎么定位并修复？",
                    "intent": "检查复盘能力和工程问题处理方式。",
                    "answer_points": [
                        "说明失败信号、定位路径、修复方案和后续监控。",
                        "避免只讲结果顺利，要能讲边界条件和取舍。",
                    ],
                    "evidence_refs": [],
                    "risk_level": "low",
                    "skills": [],
                    "source_perspective": "general_interview",
                },
                {
                    "question": "如果导师给你一个模糊需求，你会如何澄清目标、拆解任务并同步进度？",
                    "intent": "检查实习场景下的沟通与推进能力。",
                    "answer_points": [
                        "先确认验收标准、时间预算、数据或权限依赖。",
                        "用小步交付和可观测指标降低返工风险。",
                    ],
                    "evidence_refs": [],
                    "risk_level": "low",
                    "skills": [],
                    "source_perspective": "general_interview",
                },
            ],
        }

    def _gap_drills(self, job: Job, missing: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "skill": skill,
                "likely_question": f"你是否在真实项目中用过 {skill}？如果没有，相关经验是什么？",
                "honest_strategy": f"把 {skill} 明确列为待补强点，只迁移相邻经验，不说成已掌握。",
                "prep_task": f"围绕 {job.title} 做一个最小验证：阅读官方文档/教程，写 1 页方案或 30 行以内 demo，并记录限制。",
            }
            for skill in missing[:8]
        ]

    def _question_quality_judge(
        self,
        *,
        profile: Profile,
        job: Job,
        question_sets: list[dict[str, Any]],
        required: list[str],
        preferred: list[str],
        keywords: list[str],
        missing: list[str],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        questions = [question for group in question_sets for question in group.get("questions", [])]
        if not questions:
            return {
                "mode": "heuristic_v1",
                "passed": False,
                "score": 0.0,
                "rates": {},
                "issue_counts": {"empty_packet": 1},
                "sample_issues": ["面试包没有可评测题目。"],
            }

        jd_terms = self._unique([job.title, "JD", "岗位", "职责", "当前 JD", *(required or []), *(preferred or []), *(keywords or [])])
        jd_terms.extend(str(item) for item in (job.structured_jd_json or {}).get("responsibilities", [])[:4])
        profile_terms = self._unique([*self._profile_tech_stack(profile), *self._profile_project_names(profile)])
        missing_norm = {normalize_skill(item) for item in missing if normalize_skill(item)}

        project_sources = {"resume_project_evidence", "resume_project_stack", "llm_project_implementation"}
        evidence_required_sources = {"source_backed_interview_experience", "resume_project_evidence"}
        risk_sources = {"jd_gap_drill", "llm_foundation_drill"}
        boundary_terms = ["诚实", "边界", "补齐", "没有", "缺口", "未", "计划", "相邻", "不编造", "不能包装"]

        checks = {
            "jd_alignment": 0,
            "follow_up_depth": 0,
            "gap_boundary": 0,
            "project_binding": 0,
            "evidence_traceability": 0,
            "actionability": 0,
        }
        denominators = {
            "jd_alignment": len(questions),
            "follow_up_depth": len(questions),
            "gap_boundary": 0,
            "project_binding": 0,
            "evidence_traceability": 0,
            "actionability": len(questions),
        }
        issue_counts: dict[str, int] = {}
        sample_issues: list[str] = []
        normalized_questions: list[str] = []

        for question in questions:
            question_id = str(question.get("question_id") or "-")
            source = str(question.get("source_perspective") or "")
            skills = [str(item).strip() for item in question.get("skills") or [] if str(item).strip()]
            skill_norm = {normalize_skill(item) for item in skills if normalize_skill(item)}
            blob = self._quality_blob(question)
            normalized_questions.append(self._normalize_question_text(str(question.get("question") or "")))

            jd_aligned = bool(skill_norm & {normalize_skill(item) for item in jd_terms if normalize_skill(item)}) or self._text_matches_terms(blob, jd_terms)
            follow_up_depth = len(question.get("follow_ups") or []) >= 2
            risk_question = source in risk_sources or question.get("risk_level") == "high" or bool(skill_norm & missing_norm)
            if risk_question:
                denominators["gap_boundary"] += 1
            gap_boundary = (not risk_question) or self._text_matches_terms(blob, boundary_terms)
            project_question = source in project_sources
            if project_question:
                denominators["project_binding"] += 1
            project_binding = (not project_question) or bool(question.get("evidence_refs")) or self._text_matches_terms(blob, profile_terms) or bool(
                skill_norm & {normalize_skill(item) for item in profile_terms if normalize_skill(item)}
            )
            evidence_required = source in evidence_required_sources
            if evidence_required:
                denominators["evidence_traceability"] += 1
            evidence_traceability = (not evidence_required) or bool(question.get("evidence_refs"))
            actionability = len(question.get("answer_points") or []) >= 2 and len(str(question.get("question") or "")) >= 10

            results = {
                "jd_alignment": (jd_aligned, True),
                "follow_up_depth": (follow_up_depth, True),
                "gap_boundary": (gap_boundary, risk_question),
                "project_binding": (project_binding, project_question),
                "evidence_traceability": (evidence_traceability, evidence_required),
                "actionability": (actionability, True),
            }
            for name, (passed, applicable) in results.items():
                if not applicable:
                    continue
                if passed:
                    checks[name] += 1
                elif len(sample_issues) < 8:
                    issue_counts[name] = issue_counts.get(name, 0) + 1
                    sample_issues.append(f"{question_id}: {name} 未通过")
                else:
                    issue_counts[name] = issue_counts.get(name, 0) + 1

        duplicate_count = len(normalized_questions) - len({item for item in normalized_questions if item})
        duplicate_rate = round(duplicate_count / max(len(questions), 1), 4)
        if duplicate_count:
            issue_counts["duplicate_question"] = duplicate_count

        rates = {
            name: round(checks[name] / denominators[name], 4) if denominators[name] else 1.0
            for name in checks
        }
        rates["duplicate_rate"] = duplicate_rate
        rates["evidence_signal_rate"] = round(len(evidence) / max(len(questions), 1), 4)
        score = round(
            0.25 * rates["jd_alignment"]
            + 0.2 * rates["follow_up_depth"]
            + 0.2 * rates["gap_boundary"]
            + 0.15 * rates["project_binding"]
            + 0.1 * rates["evidence_traceability"]
            + 0.1 * rates["actionability"]
            - min(duplicate_rate * 0.15, 0.15),
            4,
        )
        thresholds = {
            "score": 0.82,
            "jd_alignment": 0.75,
            "follow_up_depth": 0.9,
            "gap_boundary": 0.9,
            "project_binding": 0.8,
            "evidence_traceability": 1.0,
            "duplicate_rate_max": 0.08,
        }
        passed = (
            score >= thresholds["score"]
            and rates["jd_alignment"] >= thresholds["jd_alignment"]
            and rates["follow_up_depth"] >= thresholds["follow_up_depth"]
            and rates["gap_boundary"] >= thresholds["gap_boundary"]
            and rates["project_binding"] >= thresholds["project_binding"]
            and rates["evidence_traceability"] >= thresholds["evidence_traceability"]
            and duplicate_rate <= thresholds["duplicate_rate_max"]
        )
        return {
            "mode": "heuristic_v1",
            "passed": passed,
            "score": score,
            "thresholds": thresholds,
            "rates": rates,
            "issue_counts": issue_counts,
            "sample_issues": sample_issues,
            "design_reason": "使用可解释本地 judge 作为生成质量门禁，避免为每个面试包额外调用 LLM 带来成本、延迟和不稳定性；后续可叠加 LLM-as-judge 做抽检。",
        }

    def _research_checklist(self, job: Job, required: list[str], missing: list[str]) -> list[dict[str, Any]]:
        company = job.company or "目标公司"
        focus_skills = self._unique([*required[:4], *missing[:4]])
        source_queries = [
            {
                "site": "牛客网",
                "topic": "同岗位面经",
                "query": f"site:nowcoder.com {company} {job.title} 面经 实习 {' '.join(focus_skills[:3])}",
                "why": "牛客常见校招/实习面经，适合补充同岗位高频追问。",
            },
            {
                "site": "OfferShow",
                "topic": "薪资与流程经验",
                "query": f"site:offershow.cn {company} {job.title} 面经 offer 实习",
                "why": "OfferShow 更适合补充面试轮次、岗位反馈和候选人经验。",
            },
            {
                "site": "小红书",
                "topic": "候选人经验与准备路线",
                "query": f"小红书 {company} {job.title} 面经 实习 准备",
                "why": "小红书常有候选人准备路线和面试体验，但需要警惕噪声和真实性。",
            },
            {
                "site": "搜索引擎",
                "topic": "公司与业务",
                "query": f"{company} {job.title} 业务 团队 技术栈 {' '.join(focus_skills[:4])}",
                "why": "避免面试回答只围绕个人项目，缺少对业务目标的理解。",
            },
        ]
        return source_queries

    def _coverage(
        self,
        *,
        required: list[str],
        matched: list[str],
        missing: list[str],
        question_sets: list[dict[str, Any]],
        gap_drills: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        experience_evidence: list[dict[str, Any]] | None = None,
        question_quality: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        questions = [question for group in question_sets for question in group.get("questions", [])]
        covered_skills = {
            normalize_skill(skill)
            for question in questions
            for skill in question.get("skills", [])
            if str(skill).strip()
        }
        required_norm = {normalize_skill(skill) for skill in required if normalize_skill(skill)}
        missing_norm = {normalize_skill(skill) for skill in missing if normalize_skill(skill)}
        drill_norm = {normalize_skill(item.get("skill", "")) for item in gap_drills}
        required_covered = required_norm <= (covered_skills | drill_norm) if required_norm else True
        missing_covered = missing_norm <= drill_norm if missing_norm else True
        evidence_backed = [question for question in questions if question.get("evidence_refs")]
        high_risk_questions = [question for question in questions if question.get("risk_level") == "high"]
        source_backed_questions = [
            question
            for question in questions
            if question.get("source_perspective") == "source_backed_interview_experience"
        ]
        source_perspective_counts: dict[str, int] = {}
        for question in questions:
            source = str(question.get("source_perspective") or "unknown")
            source_perspective_counts[source] = source_perspective_counts.get(source, 0) + 1
        core_perspective_counts = {
            "online_experience": source_perspective_counts.get("online_experience_research", 0)
            + source_perspective_counts.get("source_backed_interview_experience", 0),
            "resume_project_stack": source_perspective_counts.get("resume_project_stack", 0)
            + source_perspective_counts.get("resume_project_evidence", 0)
            + source_perspective_counts.get("llm_project_implementation", 0),
            "other_interview_questions": source_perspective_counts.get("general_interview", 0)
            + source_perspective_counts.get("jd_technical_depth", 0)
            + source_perspective_counts.get("jd_gap_drill", 0)
            + source_perspective_counts.get("llm_foundation_drill", 0),
        }
        preparation_angle_counts = {key: 0 for key in INTERVIEW_PREP_ANGLE_LABELS}
        for question in questions:
            angle = str(question.get("preparation_angle") or self._angle_for_source(str(question.get("source_perspective") or "")))
            preparation_angle_counts[angle] = preparation_angle_counts.get(angle, 0) + 1
        source_evidence = experience_evidence or []
        core_perspectives_passed = all(count > 0 for count in core_perspective_counts.values())
        preparation_angles_passed = all(preparation_angle_counts.get(key, 0) > 0 for key in INTERVIEW_PREP_ANGLE_LABELS)
        quality_passed = (question_quality or {}).get("passed") is True
        passed = (
            required_covered
            and missing_covered
            and core_perspectives_passed
            and preparation_angles_passed
            and quality_passed
            and len(questions) >= 6
        )
        return {
            "passed": passed,
            "required_skill_count": len(required_norm),
            "question_count": len(questions),
            "gap_drill_count": len(gap_drills),
            "research_item_count": 4,
            "source_backed_experience_count": len(source_evidence),
            "source_backed_question_count": len(source_backed_questions),
            "source_perspective_counts": dict(sorted(source_perspective_counts.items())),
            "core_perspective_counts": core_perspective_counts,
            "core_perspectives_passed": core_perspectives_passed,
            "preparation_angle_counts": preparation_angle_counts,
            "preparation_angle_labels": INTERVIEW_PREP_ANGLE_LABELS,
            "preparation_angles_passed": preparation_angles_passed,
            "question_quality_passed": quality_passed,
            "question_quality_score": (question_quality or {}).get("score", 0.0),
            "question_quality_rates": (question_quality or {}).get("rates", {}),
            "research_mode": "source_backed_and_checklist" if source_evidence else "checklist_only",
            "source_perspectives": [
                "source_backed_interview_experience",
                "online_experience_research",
                "resume_project_evidence",
                "resume_project_stack",
                "llm_project_implementation",
                "llm_foundation_drill",
                "jd_technical_depth",
                "jd_gap_drill",
                "general_interview",
            ],
            "required_skill_coverage_rate": round(
                len(required_norm & (covered_skills | drill_norm)) / max(len(required_norm), 1),
                4,
            ),
            "missing_skill_drill_rate": round(len(missing_norm & drill_norm) / max(len(missing_norm), 1), 4)
            if missing_norm
            else 1.0,
            "evidence_backed_question_rate": round(len(evidence_backed) / max(len(questions), 1), 4),
            "evidence_count": len(evidence),
            "interview_experience_evidence_count": len(source_evidence),
            "high_risk_question_count": len(high_risk_questions),
        }

    def _preparation_angles(
        self,
        *,
        profile: Profile,
        job: Job,
        question_sets: list[dict[str, Any]],
        research_checklist: list[dict[str, Any]],
        coverage: dict[str, Any],
        experience_evidence: list[dict[str, Any]],
        required: list[str],
        missing: list[str],
    ) -> list[dict[str, Any]]:
        counts = coverage.get("preparation_angle_counts") or {}
        stack = self._profile_tech_stack(profile)[:8]
        project_names = [
            str(project.get("name"))
            for project in (profile.structured_profile_json or {}).get("projects", []) or []
            if isinstance(project, dict) and str(project.get("name") or "").strip()
        ][:5]
        research_sites = [str(item.get("site")) for item in research_checklist if item.get("site")]
        source_sites = sorted({str(item.get("source_site")) for item in experience_evidence if item.get("source_site")})

        return [
            {
                "angle": "same_role_interview_experience",
                "label": INTERVIEW_PREP_ANGLE_LABELS["same_role_interview_experience"],
                "question_count": int(counts.get("same_role_interview_experience") or 0),
                "source_inputs": [
                    f"已导入同岗面经 {len(experience_evidence)} 篇" if experience_evidence else "暂无已导入正文，先生成同岗面经调研线索",
                    "调研平台：" + "、".join(research_sites[:3]),
                    "已确认来源：" + ("、".join(source_sites) if source_sites else "待人工确认"),
                ],
                "focus": [
                    "优先看牛客网、OfferShow、小红书等同岗位面经里的轮次、追问和高频技术点。",
                    "把外部问题映射到自己的简历项目证据或缺口 drill，不把搜索摘要当作已确认事实。",
                ],
                "question_source_types": ["source_backed_interview_experience", "online_experience_research"],
            },
            {
                "angle": "resume_project_tech_stack",
                "label": INTERVIEW_PREP_ANGLE_LABELS["resume_project_tech_stack"],
                "question_count": int(counts.get("resume_project_tech_stack") or 0),
                "source_inputs": [
                    "项目：" + ("、".join(project_names) if project_names else "从简历原文抽取项目主线"),
                    "技术栈：" + ("、".join(stack[:6]) if stack else "暂无结构化技术栈，使用 RAG 项目证据补齐"),
                ],
                "focus": [
                    "围绕架构位置、技术选型、替代方案、性能指标、失败边界和本人贡献深挖。",
                    "简历写到的技术必须准备可追问回答，避免只会罗列名词。",
                ],
                "question_source_types": ["resume_project_evidence", "resume_project_stack"],
            },
            {
                "angle": "other_possible_interview_questions",
                "label": INTERVIEW_PREP_ANGLE_LABELS["other_possible_interview_questions"],
                "question_count": int(counts.get("other_possible_interview_questions") or 0),
                "source_inputs": [
                    "JD 必备技能：" + ("、".join(required[:6]) if required else "未结构化出必备技能"),
                    "待补齐技能：" + ("、".join(missing[:6]) if missing else "暂无明显缺口"),
                    "岗位职责：" + self._short_text("；".join(str(item) for item in (job.structured_jd_json or {}).get("responsibilities", [])[:3]), 100),
                ],
                "focus": [
                    "覆盖 JD 技术深挖、缺口诚实披露、工程协作、动机和行为问题。",
                    "对没有证据的技能直接进入补齐计划，不用兜底话术伪装成经验。",
                ],
                "question_source_types": ["jd_technical_depth", "jd_gap_drill", "general_interview"],
            },
        ]

    def _interview_reference_links(
        self,
        *,
        job: Job,
        experience_evidence: list[dict[str, Any]],
        research_checklist: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        links: list[dict[str, Any]] = []
        for source in experience_evidence[:8]:
            url = str(source.get("source_url") or "").strip()
            title = str(source.get("title") or source.get("role_keyword") or job.title or "已导入面经").strip()
            if not title and not url:
                continue
            links.append(
                {
                    "title": title,
                    "url": url or None,
                    "site": source.get("source_site"),
                    "kind": "confirmed_imported_interview_experience",
                    "note": "用户已确认导入的面经来源，可引用正文问题；如果正文质量不足，只保留标题和链接。",
                }
            )
        for item in research_checklist[:4]:
            query = str(item.get("query") or "").strip()
            if not query:
                continue
            site = str(item.get("site") or item.get("topic") or "搜索").strip()
            links.append(
                {
                    "title": f"{site}：{item.get('topic') or job.title}",
                    "url": f"https://www.baidu.com/s?wd={quote_plus(query)}",
                    "site": site,
                    "kind": "search_reference_link",
                    "query": query,
                    "note": "只作为面经参考入口；不绕过登录、不抓取正文、不把标题当作事实证据。",
                }
            )
        return links

    def _skills(self, job: Job, key: str) -> list[str]:
        return self._unique([str(item).strip() for item in (job.structured_jd_json or {}).get(key, []) if str(item).strip()])

    def _angle_for_source(self, source_perspective: str) -> str:
        return SOURCE_PERSPECTIVE_TO_ANGLE.get(source_perspective, "other_possible_interview_questions")

    def _llm_category_for_source(self, source_perspective: str) -> str:
        if source_perspective == "llm_project_implementation":
            return "LLM 项目实现追问"
        return "LLM 八股与基础追问"

    def _evidence(self, match: MatchResult) -> list[dict[str, Any]]:
        return [
            {
                "chunk_uid": item.get("uid") or item.get("chunk_uid"),
                "chunk_type": item.get("chunk_type"),
                "source": item.get("source"),
                "text": self._short_text(item.get("text"), 280),
                "score": item.get("score"),
                "evidence_type": item.get("evidence_type") or "generic_skill",
                "polarity": item.get("polarity") or "neutral",
            }
            for item in (match.relevant_evidence_json or [])[:8]
        ]

    def _evidence_skills(self, evidence: dict[str, Any], matched: list[str]) -> list[str]:
        text = str(evidence.get("text") or "").lower()
        return [skill for skill in matched if normalize_skill(skill) in text][:4]

    def _find_evidence_for_skill(self, evidence: list[dict[str, Any]], skill: str) -> dict[str, Any] | None:
        needle = normalize_skill(skill)
        for item in evidence:
            text = str(item.get("text") or "").lower()
            if needle and needle in text and item.get("polarity") != "negative":
                return item
        return evidence[0] if evidence else None

    def _technical_answer_points(self, skill: str, evidence: dict[str, Any] | None) -> list[str]:
        points = [f"先解释 {skill} 在 JD 场景中的输入、处理过程和输出。"]
        if evidence:
            points.append(f"引用简历证据：{self._short_text(evidence.get('text'), 90)}")
        else:
            points.append("如果简历没有直接证据，说明相邻经验和补齐计划，不编造交付经历。")
        points.append("补充指标或验证方式，例如准确率、召回、延迟、异常率或人工验收。")
        return points

    def _preparation_focus(self, match: MatchResult, missing: list[str], evidence: list[dict[str, Any]]) -> list[str]:
        focus = [
            f"先准备匹配分 {match.overall_score:.1f} 的解释：哪些能力已被证据支持，哪些只是相邻经验。",
            "每个项目回答都要包含背景、你的行动、结果指标和失败/限制。",
        ]
        if missing:
            focus.append("对缺口技能准备诚实披露话术：" + "、".join(missing[:5]))
        if any(item.get("evidence_type") in {"coursework", "planned_learning", "missing_skill_disclosure"} for item in evidence):
            focus.append("RAG 命中过弱证据，面试时要主动区分课程/计划学习与真实交付。")
        return focus

    def _fit_level(self, score: float) -> str:
        if score >= 75:
            return "strong_fit"
        if score >= 55:
            return "partial_fit"
        return "weak_fit"

    def _question_risk(self, evidence: dict[str, Any]) -> str:
        if evidence.get("evidence_type") in {"missing_skill_disclosure", "planned_learning", "coursework"}:
            return "high"
        if evidence.get("polarity") == "negative":
            return "high"
        if evidence.get("evidence_type") == "adjacent_experience":
            return "medium"
        return "low"

    def _evidence_ref(self, evidence: dict[str, Any], index: int) -> dict[str, Any]:
        return {
            "ref": evidence.get("chunk_uid") or f"evidence_{index}",
            "chunk_type": evidence.get("chunk_type"),
            "evidence_type": evidence.get("evidence_type"),
            "preview": self._short_text(evidence.get("text"), 120),
        }

    def _profile_tech_stack(self, profile: Profile) -> list[str]:
        data = profile.structured_profile_json or {}
        values: list[str] = []
        values.extend(str(item) for item in data.get("skills", []) if str(item).strip())
        for project in data.get("projects", []) or []:
            if not isinstance(project, dict):
                continue
            values.extend(str(item) for item in project.get("tech_stack", []) or [] if str(item).strip())
        for exp in data.get("work_experience", []) or []:
            if not isinstance(exp, dict):
                continue
            values.extend(str(item) for item in exp.get("tech_stack", []) or [] if str(item).strip())
        return self._unique(values)

    def _profile_project_names(self, profile: Profile) -> list[str]:
        names: list[str] = []
        for project in (profile.structured_profile_json or {}).get("projects", []) or []:
            if isinstance(project, dict) and str(project.get("name") or "").strip():
                names.append(str(project.get("name")).strip())
        return self._unique(names)

    def _quality_blob(self, question: dict[str, Any]) -> str:
        values: list[str] = [
            str(question.get("question") or ""),
            str(question.get("intent") or ""),
        ]
        values.extend(str(item) for item in question.get("follow_ups") or [])
        values.extend(str(item) for item in question.get("answer_points") or [])
        values.extend(str(item) for item in question.get("skills") or [])
        return "\n".join(values).lower()

    def _text_matches_terms(self, text: str, terms: list[str]) -> bool:
        normalized_text = normalize_skill(text)
        for term in terms:
            value = str(term).strip()
            if len(value) < 2:
                continue
            normalized = normalize_skill(value)
            if normalized and normalized in normalized_text:
                return True
            if value.lower() in text:
                return True
        return False

    def _normalize_question_text(self, text: str) -> str:
        return re.sub(r"\s+", "", re.sub(r"[^\w\u4e00-\u9fff]+", "", text.lower()))

    def _unique(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            key = normalize_skill(value)
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def _short_text(self, value: Any, limit: int = 120) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "..."
