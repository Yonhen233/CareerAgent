from difflib import SequenceMatcher
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Job, MatchResult, Profile
from app.services.embedding_service import EmbeddingService, cosine_similarity, tokenize
from app.services.evidence_classifier import EvidenceClassifier
from app.services.vector_index import SQLiteVectorIndex


NEGATIVE_EVIDENCE_CUES = [
    "no shipped",
    "no api service",
    "no deployed",
    "no production",
    "did not build",
    "did not implement",
    "no ",
    "not ",
    "without ",
    "lacks ",
    "missing ",
    "read articles",
    "read papers",
    "coursework",
    "planned",
    "planning to",
    "learning about",
    "currently learning",
    "without building",
    "没有",
    "未实现",
    "未交付",
    "计划学习",
    "课程",
    "阅读",
]

POSITIVE_EVIDENCE_CUES = [
    "built",
    "implemented",
    "created",
    "designed",
    "deployed",
    "maintained",
    "delivered",
    "developed",
    "trained",
    "logged",
    "wrote",
    "构建",
    "实现",
    "开发",
    "部署",
    "维护",
    "交付",
]

SKILL_ALIASES = {
    "a/b testing": ["a/b tests", "ab testing", "experiment analysis", "experiment analysis notebooks"],
    "evaluation": ["metrics", "metric", "model evaluation", "offline evaluation"],
    "metrics": ["metric definitions", "experiment analysis", "dashboards"],
    "agent workflow": ["agent workflows", "agent system", "agent systems"],
    "guardrails": ["guardrail"],
    "data quality": ["quality checks", "validation reports"],
    "accessibility": ["accessibility checks"],
}


def normalize_skill(skill: str) -> str:
    return skill.strip().lower()


def skill_terms(skill: str) -> list[str]:
    skill_norm = normalize_skill(skill)
    aliases = SKILL_ALIASES.get(skill_norm, [])
    return [term for term in [skill_norm, *aliases] if term]


def _term_in_text(term: str, text: str) -> bool:
    if not term:
        return False
    if re.search(r"[a-z0-9]", term, flags=re.IGNORECASE):
        pattern = re.escape(term).replace(r"\ ", r"\s+")
        return re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text, flags=re.IGNORECASE) is not None
    return term in text


def fuzzy_contains(needle: str, haystack_tokens: set[str], haystack_text: str) -> bool:
    terms = skill_terms(needle)
    if not terms:
        return False
    for term in terms:
        if _term_in_text(term, haystack_text):
            return True
        if any(SequenceMatcher(None, term, token).ratio() >= 0.88 for token in haystack_tokens):
            return True
    return False


class MatcherService:
    def __init__(self) -> None:
        self.vector_index = SQLiteVectorIndex()
        self.embedding_service = EmbeddingService()
        self.evidence_classifier = EvidenceClassifier()

    def build_match_payload(self, db: Session, profile: Profile, job: Job) -> dict[str, Any]:
        profile_data = profile.structured_profile_json or {}
        job_data = job.structured_jd_json or {}

        required = [str(x).strip() for x in job_data.get("required_skills", []) if str(x).strip()]
        preferred = [str(x).strip() for x in job_data.get("preferred_skills", []) if str(x).strip()]
        all_required = required or [str(x).strip() for x in job_data.get("keywords", [])[:12] if str(x).strip()]
        resume_text = self._support_text(profile, profile_data).lower()
        resume_tokens = set(tokenize(resume_text))

        matched = [
            skill
            for skill in all_required
            if fuzzy_contains(skill, resume_tokens, resume_text)
            and self._skill_has_positive_or_neutral_support(skill, resume_text)
        ]
        missing = [skill for skill in all_required if skill not in matched]
        required_score = len(matched) / max(len(all_required), 1)

        semantic_score = self._semantic_similarity(resume_text, job.raw_jd_text)
        evidence = self.retrieve_evidence(db, profile.id, job, top_k=8)
        project_score = self._project_relevance(evidence)
        internship_score = self._internship_fit(profile, job)
        preference_score = self._preference_coverage(preferred, resume_tokens, resume_text)
        negative_penalty = self._negative_evidence_penalty(resume_text, all_required)

        overall = (
            required_score * 0.38
            + semantic_score * 0.24
            + project_score * 0.22
            + internship_score * 0.08
            + preference_score * 0.08
            - negative_penalty
        )
        overall = max(0.0, min(overall, 1.0))
        dimension_scores = {
            "required_skill_coverage": round(required_score * 100, 2),
            "semantic_similarity": round(semantic_score * 100, 2),
            "evidence_relevance": round(project_score * 100, 2),
            "internship_fit": round(internship_score * 100, 2),
            "preferred_skill_coverage": round(preference_score * 100, 2),
            "negative_evidence_penalty": round(negative_penalty * 100, 2),
        }
        return {
            "overall_score": round(overall * 100, 2),
            "dimension_scores": dimension_scores,
            "matched_skills": matched,
            "missing_skills": missing,
            "relevant_evidence": evidence,
            "suggestions": self._suggestions(missing, dimension_scores),
        }

    def create_match_result(self, db: Session, profile: Profile, job: Job) -> MatchResult:
        payload = self.build_match_payload(db, profile, job)
        result = MatchResult(
            profile_id=profile.id,
            job_id=job.id,
            overall_score=payload["overall_score"],
            dimension_scores_json=payload["dimension_scores"],
            matched_skills_json=payload["matched_skills"],
            missing_skills_json=payload["missing_skills"],
            relevant_evidence_json=payload["relevant_evidence"],
            suggestions_json=payload["suggestions"],
        )
        db.add(result)
        db.commit()
        db.refresh(result)
        return result

    def retrieve_evidence(self, db: Session, profile_id: int, job: Job, top_k: int = 8) -> list[dict[str, Any]]:
        job_data = job.structured_jd_json or {}
        query_parts = [
            job.title,
            " ".join(job_data.get("required_skills", []) or []),
            " ".join(job_data.get("keywords", []) or []),
            " ".join(job_data.get("responsibilities", [])[:4] or []),
            job.raw_jd_text[:900],
        ]
        query = "\n".join(part for part in query_parts if part)
        return [
            self.evidence_classifier.classify_dict(chunk.as_dict())
            for chunk in self.vector_index.query_profile_chunks(db, profile_id, query, top_k=top_k)
        ]

    def _semantic_similarity(self, resume_text: str, jd_text: str) -> float:
        embeddings = self.embedding_service.embed_texts([resume_text, jd_text])
        if len(embeddings.vectors) < 2:
            return 0.0
        left, right = embeddings.vectors
        return max(0.0, min(1.0, (cosine_similarity(left, right) + 1.0) / 2.0))

    def _support_text(self, profile: Profile, profile_data: dict[str, Any]) -> str:
        parts: list[str] = []
        raw_support = self._raw_support_text(profile, profile_data)
        if raw_support:
            parts.append(raw_support)
        if profile_data.get("skills"):
            parts.append("Skills: " + ", ".join(str(item) for item in profile_data.get("skills", [])))
        for project in profile_data.get("projects", []):
            if not isinstance(project, dict):
                continue
            parts.append(
                "Project evidence: "
                + " ".join(
                    str(value)
                    for value in [
                        project.get("name", ""),
                        project.get("description", ""),
                        " ".join(project.get("tech_stack", []) or []),
                        project.get("impact", ""),
                    ]
                    if value
                )
            )
        for exp in profile_data.get("work_experience", []):
            if not isinstance(exp, dict):
                continue
            parts.append(
                "Experience evidence: "
                + " ".join(
                    str(value)
                    for value in [
                        exp.get("company", ""),
                        exp.get("role", ""),
                        exp.get("details", ""),
                        " ".join(exp.get("tech_stack", []) or []),
                    ]
                    if value
                )
            )
        return "\n".join(parts)

    def _raw_support_text(self, profile: Profile, profile_data: dict[str, Any]) -> str:
        raw_text = profile.raw_resume_text or str(profile_data.get("raw_text") or "")
        target_roles = [normalize_skill(item) for item in profile.target_roles_json or profile_data.get("target_roles", [])]
        exact_metadata = {
            normalize_skill(value)
            for value in [
                profile.name,
                profile.email,
                profile.phone,
                profile.headline,
                profile_data.get("headline"),
            ]
            if value
        }
        filtered: list[str] = []
        for line in raw_text.splitlines():
            stripped = line.strip()
            lowered = normalize_skill(stripped)
            if not stripped:
                continue
            if lowered in exact_metadata:
                continue
            if lowered.startswith(("target roles:", "target role:", "目标岗位", "求职意向")):
                continue
            if self._is_target_intent_line(lowered, target_roles):
                continue
            filtered.append(stripped)
        return "\n".join(filtered)

    def _is_target_intent_line(self, lowered_line: str, target_roles: list[str]) -> bool:
        if not lowered_line or not target_roles:
            return False
        intent_cues = ["target", "candidate", "seeking", "applying", "目标", "求职", "候选人", "应聘"]
        return any(role and role in lowered_line for role in target_roles) and any(
            cue in lowered_line for cue in intent_cues
        )

    def _project_relevance(self, evidence: list[dict[str, Any]]) -> float:
        if not evidence:
            return 0.35
        weighted = 0.0
        for item in evidence[:6]:
            base = float(item.get("score") or 0.0)
            text = str(item.get("text") or "").lower()
            evidence_type = str(item.get("evidence_type") or "")
            if evidence_type in {"missing_skill_disclosure", "planned_learning", "coursework"}:
                base -= 0.38
            elif evidence_type == "metric_evidence":
                base += 0.22
            elif evidence_type == "shipped_project":
                base += 0.18
            elif evidence_type == "adjacent_experience":
                base += 0.06
            elif self._contains_negative_evidence(text):
                base -= 0.35
            if item.get("chunk_type") in {"project", "experience"} and evidence_type not in {
                "missing_skill_disclosure",
                "planned_learning",
                "coursework",
            }:
                base += 0.18
            weighted += max(0.0, min(base, 1.0))
        return max(0.0, min(weighted / min(len(evidence), 6), 1.0))

    def _skill_has_positive_or_neutral_support(self, skill: str, resume_text: str) -> bool:
        if not normalize_skill(skill):
            return False
        sentences = self._sentences_with_skill(resume_text.lower(), skill)
        if not sentences:
            return True
        positive = [
            sentence
            for sentence in sentences
            if self._contains_positive_evidence(sentence) and not self._contains_negative_evidence(sentence)
        ]
        if positive:
            return True
        non_negative = [sentence for sentence in sentences if not self._contains_negative_evidence(sentence)]
        return bool(non_negative)

    def _negative_evidence_penalty(self, resume_text: str, required: list[str]) -> float:
        text = resume_text.lower()
        if not self._contains_negative_evidence(text):
            return 0.0
        negative_required_mentions = 0
        for skill in required:
            sentences = self._sentences_with_skill(text, skill)
            if sentences and all(self._contains_negative_evidence(sentence) for sentence in sentences):
                negative_required_mentions += 1
        if negative_required_mentions == 0:
            return 0.04
        ratio = negative_required_mentions / max(len(required), 1)
        return min(0.18, 0.06 + ratio * 0.16)

    def _sentences_with_skill(self, text: str, skill: str) -> list[str]:
        terms = skill_terms(skill)
        sentences = [
            sentence.strip()
            for sentence in re.split(r"[。！？!?；;\n.]+", text)
            if sentence.strip()
        ]
        return [sentence for sentence in sentences if any(_term_in_text(term, sentence) for term in terms)]

    def _contains_negative_evidence(self, text: str) -> bool:
        lowered = text.lower()
        return any(cue in lowered for cue in NEGATIVE_EVIDENCE_CUES)

    def _contains_positive_evidence(self, text: str) -> bool:
        lowered = text.lower()
        return any(cue in lowered for cue in POSITIVE_EVIDENCE_CUES)

    def _internship_fit(self, profile: Profile, job: Job) -> float:
        text = f"{job.title} {job.job_type or ''} {job.raw_jd_text}".lower()
        if not any(token in text for token in ["intern", "internship", "实习"]):
            return 0.75
        profile_text = f"{profile.headline or ''} {profile.raw_resume_text}".lower()
        if any(token in profile_text for token in ["intern", "实习", "student", "university", "本科", "硕士"]):
            return 0.95
        return 0.72

    def _preference_coverage(self, preferred: list[str], resume_tokens: set[str], resume_text: str) -> float:
        if not preferred:
            return 0.7
        matched = [skill for skill in preferred if fuzzy_contains(skill, resume_tokens, resume_text)]
        return len(matched) / max(len(preferred), 1)

    def _suggestions(self, missing: list[str], dimensions: dict[str, float]) -> list[str]:
        suggestions: list[str] = []
        if missing:
            suggestions.append("补足或补证 JD 里的关键技能：" + "、".join(missing[:8]))
        if dimensions["evidence_relevance"] < 55:
            suggestions.append("把最相关的 Agent/RAG/后端项目提前，并写清工具链、闭环指标和落地效果。")
        if dimensions["semantic_similarity"] < 60:
            suggestions.append("在项目描述中自然覆盖岗位关键词，避免只罗列技术名词。")
        if dimensions.get("negative_evidence_penalty", 0) > 0:
            suggestions.append("区分真实交付与课程/计划/阅读经历，避免把未交付内容当成匹配证据。")
        if not suggestions:
            suggestions.append("匹配度较高，建议重点压缩无关经历并强化可量化结果。")
        return suggestions
