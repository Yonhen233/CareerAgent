import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMClient, format_exception
from app.models.entities import Job, Profile, ResumeChunk
from app.services.matcher import MatcherService
from app.services.text_splitter import ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


METRIC_RE = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:%|ms|s|秒|分钟|小时|天|周|月|年|qps|rps|w|万|k|kb|mb|gb|人|次|个|条|篇|轮|倍))",
    re.IGNORECASE,
)

ACTION_CUES = [
    "实现",
    "开发",
    "设计",
    "构建",
    "优化",
    "部署",
    "上线",
    "评测",
    "监控",
    "built",
    "implemented",
    "designed",
    "deployed",
    "optimized",
    "measured",
]

RISK_CUES = [
    "熟悉",
    "了解",
    "学习过",
    "计划",
    "阅读过",
    "负责参与",
    "etc",
    "等等",
    "各种",
]


class ResumeReviewService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMClient()
        self.matcher = MatcherService()
        self.vector_index = SQLiteVectorIndex()
        self.splitter = ResumeTextSplitter(self.settings.chunk_size, self.settings.chunk_overlap)

    async def review_profile(
        self,
        db: Session,
        *,
        profile: Profile,
        job: Job | None = None,
        include_llm: bool = True,
    ) -> dict[str, Any]:
        profile_data = profile.structured_profile_json or {}
        text = self._profile_text(profile, profile_data)
        dimensions = self._general_dimensions(profile, profile_data, text)
        strengths = self._general_strengths(profile, profile_data, text, dimensions)
        issues = self._general_issues(profile, profile_data, text, dimensions)
        suggestions = self._general_suggestions(profile, profile_data, dimensions)
        target_alignment: dict[str, Any] = {}
        rag_evidence: list[dict[str, Any]] = []
        trace: dict[str, Any] = {
            "scoring_version": "resume_review_v1",
            "llm_used": False,
            "rag_used": False,
            "rag_reason": "岗位针对性建议需要从简历 chunks 中检索与 JD 最相关的证据。",
        }

        if job is not None:
            self._ensure_profile_chunks(db, profile)
            match_payload = self.matcher.build_match_payload(db, profile, job)
            rag_evidence = match_payload.get("relevant_evidence", [])[:8]
            target_alignment = {
                "job_title": job.title,
                "company": job.company,
                "match_score": match_payload.get("overall_score", 0),
                "matched_skills": match_payload.get("matched_skills", []),
                "missing_skills": match_payload.get("missing_skills", []),
                "match_dimensions": match_payload.get("dimension_scores", {}),
            }
            dimensions["target_alignment"] = round(float(match_payload.get("overall_score") or 0), 2)
            issues.extend(self._target_issues(match_payload))
            suggestions.extend(self._target_suggestions(match_payload, rag_evidence))
            trace["rag_used"] = True
            trace["rag_top_k"] = len(rag_evidence)
            trace["rag_chunk_types"] = sorted({str(item.get("chunk_type") or "") for item in rag_evidence if item})

        overall = self._overall_score(dimensions, targeted=job is not None)
        result = {
            "profile_id": profile.id,
            "job_id": job.id if job is not None else None,
            "review_type": "targeted" if job is not None else "general",
            "overall_score": overall,
            "grade": self._grade(overall),
            "dimension_scores": dimensions,
            "strengths": strengths[:5],
            "issues": self._dedupe_issue_dicts(issues)[:8],
            "suggestions": self._dedupe_suggestion_dicts(suggestions)[:10],
            "target_alignment": target_alignment,
            "rag_evidence": self._compact_evidence(rag_evidence),
            "trace": trace,
        }

        if include_llm and self.llm.available:
            result = await self._enhance_with_llm(db, result, profile, job, text)
        return result

    def _ensure_profile_chunks(self, db: Session, profile: Profile) -> None:
        exists = db.query(ResumeChunk.id).filter(ResumeChunk.profile_id == profile.id).first()
        if exists:
            return
        chunks = self.splitter.build_resume_chunks(profile.structured_profile_json or {})
        self.vector_index.upsert_profile_chunks(db, profile.id, chunks)

    async def _enhance_with_llm(
        self,
        db: Session,
        result: dict[str, Any],
        profile: Profile,
        job: Job | None,
        text: str,
    ) -> dict[str, Any]:
        system_prompt = (
            "你是中文求职简历评审助手。必须只基于输入的简历、JD 和 RAG 证据提出建议，"
            "不要编造经历、技能、公司、日期、样本量、百分比、耗时或任何量化结果。"
            "原简历没有指标时只能写“待补充真实数据”，不得给出假设数字或看似可直接使用的虚构示例。"
            "返回严格 JSON。"
        )
        user_prompt = json.dumps(
            {
                "task": "输出更具体的简历修改建议，保持中文，按优先级排序。",
                "profile_id": profile.id,
                "job": {
                    "id": job.id,
                    "title": job.title,
                    "company": job.company,
                    "structured_jd": job.structured_jd_json,
                }
                if job
                else None,
                "resume_excerpt": text[:4500],
                "current_review": {
                    "overall_score": result["overall_score"],
                    "dimension_scores": result["dimension_scores"],
                    "issues": result["issues"],
                    "suggestions": result["suggestions"],
                    "rag_evidence": result["rag_evidence"],
                    "target_alignment": result["target_alignment"],
                },
                "json_schema": {
                    "strengths": ["最多 4 条，说明为什么是优势"],
                    "suggestions": [
                        {
                            "priority": "high|medium|low",
                            "section": "建议修改的栏目",
                            "problem": "当前问题",
                            "advice": "具体怎么改",
                            "example_rewrite": "可直接参考的中文 bullet，不得编造新事实",
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )
        try:
            parsed = await self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.1,
                max_tokens=1600,
                db=db,
                trace_name="resume_review.enhance_suggestions",
            )
        except Exception as exc:
            if not self.settings.llm_fallback_enabled:
                raise
            result["trace"]["llm_error"] = format_exception(exc)
            return result

        llm_suggestions = parsed.get("suggestions", [])
        if isinstance(llm_suggestions, list):
            safe_llm_suggestions, rejected_suggestions = self._ground_llm_suggestions(
                llm_suggestions,
                source_text=text,
            )
            result["suggestions"] = self._dedupe_suggestion_dicts(
                [*safe_llm_suggestions, *result.get("suggestions", [])]
            )[:10]
            result["trace"]["llm_rejected_suggestions"] = rejected_suggestions
        llm_strengths = parsed.get("strengths", [])
        if isinstance(llm_strengths, list) and llm_strengths:
            result["strengths"] = [str(item) for item in llm_strengths if str(item).strip()][:5]
        result["trace"]["llm_used"] = True
        return result

    def _ground_llm_suggestions(
        self,
        suggestions: list[Any],
        *,
        source_text: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        source_numbers = set(re.findall(r"\d+(?:\.\d+)?", source_text))
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for raw in suggestions:
            if not isinstance(raw, dict):
                continue
            claim_text = " ".join(
                str(raw.get(key) or "")
                for key in ("problem", "advice", "example_rewrite")
            )
            generated_numbers = set(re.findall(r"\d+(?:\.\d+)?", claim_text))
            unsupported_numbers = sorted(generated_numbers - source_numbers)
            if unsupported_numbers:
                rejected.append(
                    {
                        "reason": "unsupported_numeric_claim",
                        "numbers": unsupported_numbers,
                        "section": str(raw.get("section") or "简历"),
                    }
                )
                continue
            accepted.append(raw)
        return accepted, rejected

    def _profile_text(self, profile: Profile, profile_data: dict[str, Any]) -> str:
        parts = [profile.raw_resume_text or "", profile.headline or ""]
        for key in ["self_summary", "raw_text"]:
            value = profile_data.get(key)
            if value:
                parts.append(str(value))
        for item in profile_data.get("skills", []) or []:
            parts.append(str(item))
        for key in ["projects", "work_experience", "campus_experience", "education"]:
            for item in profile_data.get(key, []) or []:
                if isinstance(item, dict):
                    parts.append(" ".join(str(value) for value in item.values() if value))
        return "\n".join(part for part in parts if part).strip()

    def _general_dimensions(self, profile: Profile, profile_data: dict[str, Any], text: str) -> dict[str, float]:
        completeness_checks = [
            bool(profile.name),
            bool(profile.email),
            bool(profile.phone),
            bool(profile.headline or profile_data.get("headline")),
            bool(profile.target_roles_json or profile_data.get("target_roles")),
            bool(profile_data.get("skills")),
            bool(profile_data.get("projects") or profile_data.get("work_experience")),
            bool(profile_data.get("education")),
        ]
        completeness = sum(completeness_checks) / len(completeness_checks) * 100
        evidence_strength = self._evidence_strength(profile_data, text)
        metric_density = self._metric_density(text)
        keyword_clarity = self._keyword_clarity(profile, profile_data, text)
        readability = self._readability(text, profile_data)
        risk_control = self._risk_control(profile_data, text)
        return {
            "profile_completeness": round(completeness, 2),
            "evidence_strength": round(evidence_strength, 2),
            "metric_density": round(metric_density, 2),
            "keyword_clarity": round(keyword_clarity, 2),
            "readability": round(readability, 2),
            "risk_control": round(risk_control, 2),
        }

    def _evidence_strength(self, profile_data: dict[str, Any], text: str) -> float:
        projects = [item for item in profile_data.get("projects", []) or [] if isinstance(item, dict)]
        experiences = [item for item in profile_data.get("work_experience", []) or [] if isinstance(item, dict)]
        scored_items = projects + experiences
        if not scored_items:
            return 35.0 if text else 0.0
        scores = []
        for item in scored_items:
            blob = " ".join(str(value) for value in item.values() if value)
            score = 38.0
            if len(blob) >= 120:
                score += 18
            if any(cue.lower() in blob.lower() for cue in ACTION_CUES):
                score += 18
            if METRIC_RE.search(blob):
                score += 16
            if item.get("tech_stack"):
                score += 10
            scores.append(min(score, 100.0))
        return sum(scores) / len(scores)

    def _metric_density(self, text: str) -> float:
        if not text:
            return 0.0
        metrics = METRIC_RE.findall(text)
        paragraphs = [part for part in re.split(r"[\n。；;]+", text) if part.strip()]
        ratio = len(metrics) / max(len(paragraphs), 1)
        return min(100.0, 35.0 + ratio * 130.0) if metrics else 28.0

    def _keyword_clarity(self, profile: Profile, profile_data: dict[str, Any], text: str) -> float:
        skills = [str(item).strip() for item in profile_data.get("skills", []) or [] if str(item).strip()]
        roles = [str(item).strip() for item in profile.target_roles_json or profile_data.get("target_roles", []) if str(item).strip()]
        score = 35.0
        if roles:
            score += 20
        if len(skills) >= 5:
            score += 22
        elif skills:
            score += 12
        project_hits = sum(1 for skill in skills if skill.lower() in text.lower())
        if skills:
            score += min(project_hits / max(len(skills), 1) * 23, 23)
        return min(score, 100.0)

    def _readability(self, text: str, profile_data: dict[str, Any]) -> float:
        if not text:
            return 0.0
        length = len(text)
        score = 78.0
        if length < 650:
            score -= 18
        if length > 5200:
            score -= 14
        section_count = sum(1 for key in ["education", "skills", "projects", "work_experience"] if profile_data.get(key))
        score += min(section_count * 4, 16)
        long_lines = [line for line in text.splitlines() if len(line) > 160]
        score -= min(len(long_lines) * 3, 15)
        return max(0.0, min(score, 100.0))

    def _risk_control(self, profile_data: dict[str, Any], text: str) -> float:
        score = 92.0
        prompt_injection = profile_data.get("prompt_injection") or {}
        if prompt_injection.get("detected"):
            score -= 35
        cue_count = sum(text.lower().count(cue.lower()) for cue in RISK_CUES)
        score -= min(cue_count * 4, 24)
        if "http" in text.lower() and not profile_data.get("portfolio_links"):
            score -= 4
        return max(0.0, min(score, 100.0))

    def _general_strengths(
        self,
        profile: Profile,
        profile_data: dict[str, Any],
        text: str,
        dimensions: dict[str, float],
    ) -> list[str]:
        strengths: list[str] = []
        if dimensions["evidence_strength"] >= 75:
            strengths.append("项目或经历描述有较明确的动作、技术栈和交付证据。")
        if dimensions["keyword_clarity"] >= 75:
            strengths.append("目标岗位和技能关键词比较清楚，方便后续做岗位匹配。")
        if dimensions["metric_density"] >= 70:
            strengths.append("简历中已有量化结果，适合进一步强化为 STAR bullet。")
        if profile_data.get("projects") and profile_data.get("skills"):
            strengths.append("项目经历和技能列表能够相互支撑，不只是单独堆关键词。")
        if not strengths:
            strengths.append("已有基础简历结构，可以继续补充项目证据和岗位关键词。")
        return strengths

    def _general_issues(
        self,
        profile: Profile,
        profile_data: dict[str, Any],
        text: str,
        dimensions: dict[str, float],
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        if dimensions["profile_completeness"] < 75:
            missing = []
            if not profile.email:
                missing.append("邮箱")
            if not profile.phone:
                missing.append("电话")
            if not profile_data.get("education"):
                missing.append("教育经历")
            if not (profile_data.get("projects") or profile_data.get("work_experience")):
                missing.append("项目/实习经历")
            issues.append({"severity": "high", "section": "基础信息", "problem": "关键信息不完整：" + "、".join(missing)})
        if dimensions["evidence_strength"] < 65:
            issues.append({"severity": "high", "section": "项目/实习经历", "problem": "经历描述偏概括，缺少动作、技术方案、结果三段证据。"})
        if dimensions["metric_density"] < 55:
            issues.append({"severity": "medium", "section": "项目/实习经历", "problem": "量化指标不足，面试官难以判断项目规模和效果。"})
        if dimensions["keyword_clarity"] < 65:
            issues.append({"severity": "medium", "section": "技能/求职意向", "problem": "目标岗位关键词和项目证据之间的对应关系不够明显。"})
        if dimensions["risk_control"] < 75:
            issues.append({"severity": "medium", "section": "事实边界", "problem": "存在偏泛化或低证据表达，建议改成可证明的交付事实。"})
        return issues

    def _general_suggestions(
        self,
        profile: Profile,
        profile_data: dict[str, Any],
        dimensions: dict[str, float],
    ) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        if dimensions["evidence_strength"] < 75:
            suggestions.append(
                {
                    "priority": "high",
                    "section": "项目经历",
                    "advice": "每个核心项目至少写清：目标场景、你的职责、关键技术方案、上线/评测结果。",
                    "example_rewrite": (
                        "【项目名】：面向【真实场景】实现【本人完成的模块】，"
                        "使用【真实技术栈】解决【具体问题】，结果为【待补充真实数据或可验证结论】。"
                    ),
                }
            )
        if dimensions["metric_density"] < 65:
            suggestions.append(
                {
                    "priority": "high",
                    "section": "项目/实习经历",
                    "advice": "把“做了什么”改成“做到什么程度”，补充样本量、耗时、通过率、召回率、延迟、覆盖率等指标。",
                    "example_rewrite": (
                        "构建【样本量待补充】的评测集，按【真实评测指标】比较方案，"
                        "最终结果为【待补充真实数据】。"
                    ),
                }
            )
        if not profile_data.get("self_summary"):
            suggestions.append(
                {
                    "priority": "medium",
                    "section": "个人总结",
                    "advice": "增加 2-3 句面向目标岗位的总结，突出最相关项目、技术栈和工程化能力。",
                    "example_rewrite": self._grounded_summary_example(profile, profile_data),
                }
            )
        return suggestions

    def _grounded_summary_example(self, profile: Profile, profile_data: dict[str, Any]) -> str:
        roles = list(profile.target_roles_json or profile_data.get("target_roles") or [])
        target = str(roles[0] if roles else profile.headline or "目标岗位")
        skills = [str(item) for item in (profile_data.get("skills") or []) if str(item).strip()][:6]
        projects = [item for item in (profile_data.get("projects") or []) if isinstance(item, dict)]
        project_name = str(projects[0].get("name") or "").strip() if projects else ""
        project_part = f"在 {project_name} 项目中" if project_name else "在已有项目中"
        skill_part = "、".join(skills) if skills else "【真实技术栈待补充】"
        return f"目标岗位：{target}；{project_part}实际使用 {skill_part}，具体成果以简历中的可验证经历为准。"

    def _target_issues(self, match_payload: dict[str, Any]) -> list[dict[str, Any]]:
        missing = match_payload.get("missing_skills", []) or []
        issues = []
        if missing:
            issues.append(
                {
                    "severity": "high",
                    "section": "岗位匹配",
                    "problem": "JD 关键技能缺少简历证据：" + "、".join(str(item) for item in missing[:8]),
                }
            )
        dimensions = match_payload.get("dimension_scores", {}) or {}
        if float(dimensions.get("evidence_relevance", 0) or 0) < 60:
            issues.append({"severity": "high", "section": "项目排序", "problem": "RAG 检索到的高相关证据不够强，最相关项目需要前置并写细。"})
        return issues

    def _target_suggestions(
        self,
        match_payload: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        suggestions = []
        missing = [str(item) for item in match_payload.get("missing_skills", []) or []]
        if missing:
            suggestions.append(
                {
                    "priority": "high",
                    "section": "技能与项目证据",
                    "advice": "不要只在技能列表补词；如果确实做过，把缺失技能落到项目 bullet 中，并说明场景和结果：" + "、".join(missing[:6]),
                    "example_rewrite": f"围绕 JD 缺口补充可证明经历：{missing[0]} 在哪个模块使用、解决了什么问题、结果如何。",
                }
            )
        if evidence:
            top = evidence[0]
            suggestions.append(
                {
                    "priority": "medium",
                    "section": "项目经历",
                    "advice": f"RAG 最相关证据来自「{top.get('chunk_type', '简历片段')}」，建议把这段经历放到更靠前的位置并扩写结果。",
                    "example_rewrite": str(top.get("text") or "")[:180],
                }
            )
        return suggestions

    def _overall_score(self, dimensions: dict[str, float], *, targeted: bool) -> float:
        weights = {
            "profile_completeness": 0.16,
            "evidence_strength": 0.24,
            "metric_density": 0.16,
            "keyword_clarity": 0.16,
            "readability": 0.14,
            "risk_control": 0.14,
        }
        if targeted:
            weights = {
                "profile_completeness": 0.10,
                "evidence_strength": 0.17,
                "metric_density": 0.12,
                "keyword_clarity": 0.11,
                "readability": 0.10,
                "risk_control": 0.10,
                "target_alignment": 0.30,
            }
        return round(sum(float(dimensions.get(key, 0) or 0) * weight for key, weight in weights.items()), 2)

    def _grade(self, score: float) -> str:
        if score >= 88:
            return "优秀"
        if score >= 75:
            return "较好"
        if score >= 60:
            return "可改进"
        return "风险较高"

    def _compact_evidence(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "chunk_uid": item.get("chunk_uid"),
                "chunk_type": item.get("chunk_type"),
                "score": item.get("score"),
                "evidence_type": item.get("evidence_type"),
                "text": str(item.get("text") or "")[:360],
            }
            for item in evidence[:6]
        ]

    def _dedupe_issue_dicts(self, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        deduped = []
        for issue in issues:
            key = (str(issue.get("section") or ""), str(issue.get("problem") or ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(issue)
        return deduped

    def _dedupe_suggestion_dicts(self, suggestions: list[Any]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for raw in suggestions:
            if not isinstance(raw, dict):
                continue
            advice = str(raw.get("advice") or raw.get("problem") or "").strip()
            section = str(raw.get("section") or "简历").strip()
            if not advice:
                continue
            key = (section, advice)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(
                {
                    "priority": str(raw.get("priority") or "medium"),
                    "section": section,
                    "problem": str(raw.get("problem") or ""),
                    "advice": advice,
                    "example_rewrite": str(raw.get("example_rewrite") or ""),
                }
            )
        return deduped
