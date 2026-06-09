from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import InterviewPrep, Job, MatchResult, Profile
from app.services.interview_experience import InterviewExperienceService
from app.services.matcher import MatcherService, normalize_skill


class InterviewPrepService:
    """Build an evidence-backed interview preparation packet from Profile, JD, and match trace."""

    def __init__(
        self,
        matcher: MatcherService | None = None,
        experience_service: InterviewExperienceService | None = None,
    ) -> None:
        self.matcher = matcher or MatcherService()
        self.experience_service = experience_service or InterviewExperienceService()

    def create_interview_prep(
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
            self._technical_questions(job, evidence, matched, required, preferred, keywords),
            self._gap_questions(job, missing),
            self._collaboration_questions(job),
            self._general_interview_questions(profile, job),
        ]
        question_sets = [item for item in question_sets if item["questions"]]
        self._attach_question_ids(question_sets)
        gap_drills = self._gap_drills(job, missing)
        research_checklist = self._research_checklist(job, required, missing)
        coverage = self._coverage(
            required=required,
            matched=matched,
            missing=missing,
            question_sets=question_sets,
            gap_drills=gap_drills,
            evidence=evidence,
            experience_evidence=experience_evidence,
        )
        source_evidence = [*experience_evidence, *evidence]
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
            generation_mode="structured_rules_v2_source_backed",
        )
        db.add(prep)
        db.commit()
        db.refresh(prep)
        return prep

    def _attach_question_ids(self, question_sets: list[dict[str, Any]]) -> None:
        for group_index, group in enumerate(question_sets, start=1):
            for question_index, question in enumerate(group.get("questions") or [], start=1):
                question.setdefault("question_id", f"q{group_index:02d}_{question_index:02d}")

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
                "question": f"结合牛客网、OfferShow、小红书同岗位面经，{job.title} 常见追问可能集中在哪些技术和项目细节？",
                "intent": "把面试准备从单一 JD 扩展到同岗位真实面经，但不假装已经抓取到具体帖子。",
                "answer_points": [
                    f"先围绕 {focus_text} 归纳可能高频题。",
                    "把每个外部面经问题映射到自己的项目证据或缺口 drill。",
                    "记录来源链接和发布时间，避免用过期或不相关岗位面经误导准备。",
                ],
                "evidence_refs": [],
                "risk_level": "medium",
                "skills": focus[:4],
                "source_perspective": "online_experience_research",
            },
            {
                "question": f"如果同岗位面经问到 {focus_text} 的底层原理，你准备用哪个项目例子回答？",
                "intent": "把外部高频问题和简历项目绑定，避免只背题。",
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
            "resume_project_stack": source_perspective_counts.get("resume_project_stack", 0),
            "other_interview_questions": source_perspective_counts.get("general_interview", 0)
            + source_perspective_counts.get("jd_technical_depth", 0)
            + source_perspective_counts.get("jd_gap_drill", 0),
        }
        source_evidence = experience_evidence or []
        core_perspectives_passed = all(count > 0 for count in core_perspective_counts.values())
        passed = required_covered and missing_covered and core_perspectives_passed and len(questions) >= 6
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
            "research_mode": "source_backed_and_checklist" if source_evidence else "checklist_only",
            "source_perspectives": [
                "source_backed_interview_experience",
                "online_experience_research",
                "resume_project_evidence",
                "resume_project_stack",
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

    def _skills(self, job: Job, key: str) -> list[str]:
        return self._unique([str(item).strip() for item in (job.structured_jd_json or {}).get(key, []) if str(item).strip()])

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
