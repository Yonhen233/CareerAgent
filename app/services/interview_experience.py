from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import InterviewExperience, Job
from app.services.matcher import normalize_skill


QUESTION_MARKERS = (
    "问",
    "问题",
    "面试官问",
    "被问到",
    "追问",
    "Q:",
    "q:",
    "Question:",
)

ROUND_MARKERS = (
    "笔试",
    "一面",
    "二面",
    "三面",
    "技术面",
    "主管面",
    "HR面",
    "hr面",
    "群面",
)

KNOWN_SOURCE_SITES = {
    "牛客": "牛客网",
    "牛客网": "牛客网",
    "nowcoder": "牛客网",
    "offershow": "OfferShow",
    "offer show": "OfferShow",
    "小红书": "小红书",
    "xiaohongshu": "小红书",
    "rednote": "小红书",
}

TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "Agent": ("agent", "智能体", "工具调用", "多智能体"),
    "RAG": ("rag", "检索增强", "知识库", "召回"),
    "LLM": ("llm", "大模型", "提示词", "prompt"),
    "Embedding": ("embedding", "向量", "语义检索"),
    "Reranker": ("reranker", "重排", "cross-encoder"),
    "FastAPI": ("fastapi",),
    "Python": ("python",),
    "SQL": ("sql",),
    "SQLite": ("sqlite",),
    "Evaluation": ("evaluation", "评测", "指标", "准确率", "召回率"),
    "Guardrail": ("guardrail", "风控", "幻觉", "事实校验"),
    "Tool Calling": ("tool calling", "function calling", "工具调用"),
    "LangGraph": ("langgraph",),
    "MCP": ("mcp",),
    "React": ("react",),
    "TypeScript": ("typescript", "ts"),
    "Accessibility": ("accessibility", "无障碍", "a11y"),
    "Playwright": ("playwright",),
    "Airflow": ("airflow", "调度"),
    "Spark": ("spark",),
    "Kafka": ("kafka",),
    "Recommendation": ("推荐", "推荐系统", "recommendation"),
    "Ranking": ("ranking", "排序"),
    "CTR": ("ctr", "点击率"),
    "Feature Store": ("feature store", "特征平台", "特征库"),
    "A/B Testing": ("a/b", "ab实验", "ab 测试", "实验指标"),
    "Docker": ("docker",),
    "Kubernetes": ("kubernetes", "k8s"),
    "MLflow": ("mlflow",),
}


class InterviewExperienceService:
    """Import and normalize same-role interview experience notes.

    The service is intentionally source-backed: it extracts questions and topics
    from imported text, but does not invent specific platform posts when the
    text has no evidence.
    """

    def create_experience(
        self,
        db: Session,
        *,
        source_site: str,
        raw_text: str,
        job: Job | None = None,
        source_url: str | None = None,
        title: str | None = None,
        company: str | None = None,
        role_keyword: str | None = None,
    ) -> InterviewExperience:
        text = self._normalize_text(raw_text)
        if len(text) < 20:
            raise ValueError("Interview experience text is too short to extract reliable questions.")

        normalized_site = self.normalize_source_site(source_site)
        extracted = self.extract(text)
        row = InterviewExperience(
            job_id=job.id if job else None,
            source_site=normalized_site,
            source_url=source_url,
            title=title,
            company=company or (job.company if job else None),
            role_keyword=role_keyword or (job.title if job else None),
            raw_text=text,
            extracted_questions_json=extracted["questions"],
            topics_json=extracted["topics"],
            rounds_json=extracted["rounds"],
            credibility_json=self._credibility(
                source_site=normalized_site,
                source_url=source_url,
                text=text,
                question_count=len(extracted["questions"]),
                topic_count=len(extracted["topics"]),
            ),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def extract(self, text: str) -> dict[str, Any]:
        normalized = self._normalize_text(text)
        rounds = self._extract_rounds(normalized)
        topics = self._extract_topics(normalized)
        questions = self._extract_questions(normalized, topics)
        return {
            "questions": questions,
            "topics": topics,
            "rounds": rounds,
        }

    def find_relevant_for_job(
        self,
        db: Session,
        *,
        job: Job,
        experience_ids: list[int] | None = None,
        limit: int = 6,
    ) -> list[tuple[InterviewExperience, float]]:
        query = db.query(InterviewExperience)
        if experience_ids is not None:
            if not experience_ids:
                return []
            query = query.filter(InterviewExperience.id.in_(experience_ids))
        rows = query.order_by(InterviewExperience.created_at.desc()).limit(200).all()
        scored = []
        for row in rows:
            score = self._relevance_score(row, job, forced=bool(experience_ids))
            if score > 0:
                scored.append((row, score))
        scored.sort(key=lambda item: (item[1], item[0].created_at), reverse=True)
        return scored[:limit]

    def to_evidence(self, row: InterviewExperience, relevance_score: float) -> dict[str, Any]:
        questions = row.extracted_questions_json or []
        return {
            "evidence_type": "interview_experience",
            "source_id": row.id,
            "source_site": row.source_site,
            "source_url": row.source_url,
            "title": row.title,
            "company": row.company,
            "role_keyword": row.role_keyword,
            "topics": row.topics_json or [],
            "rounds": row.rounds_json or [],
            "questions": questions[:6],
            "credibility": row.credibility_json or {},
            "relevance_score": round(relevance_score, 4),
            "text_preview": self._short_text(row.raw_text, 240),
        }

    def normalize_source_site(self, source_site: str) -> str:
        raw = str(source_site or "").strip()
        lowered = raw.lower()
        for key, value in KNOWN_SOURCE_SITES.items():
            if key.lower() in lowered:
                return value
        return raw or "unknown"

    def _extract_questions(self, text: str, all_topics: list[str]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        current_round = None
        for line in self._candidate_lines(text):
            line_round = self._line_round(line)
            if line_round:
                current_round = line_round
            if not self._looks_like_question(line):
                continue
            question = self._clean_question(line)
            if len(question) < 6:
                continue
            topics = self._extract_topics(question) or all_topics[:3]
            candidates.append(
                {
                    "question": question,
                    "round": current_round,
                    "topics": topics[:5],
                    "source_quote": self._short_text(line, 160),
                }
            )
        return self._dedupe_questions(candidates)[:16]

    def _candidate_lines(self, text: str) -> list[str]:
        parts = []
        for block in re.split(r"[\n\r]+", text):
            block = block.strip()
            if not block:
                continue
            sentence_parts = [part.strip() for part in re.split(r"(?<=[。！？!?；;])\s*", block) if part.strip()]
            if len(sentence_parts) > 1:
                parts.extend(sentence_parts)
            elif len(block) <= 140:
                parts.append(block)
                continue
            else:
                parts.extend(sentence_parts)
        return parts

    def _looks_like_question(self, line: str) -> bool:
        stripped = line.strip()
        if "?" in stripped or "？" in stripped:
            return True
        return any(marker in stripped for marker in QUESTION_MARKERS) and len(stripped) <= 180

    def _clean_question(self, line: str) -> str:
        cleaned = re.sub(r"^\s*[-*•\d.、）)]+", "", line).strip()
        cleaned = re.sub(r"^(Q[:：]|问[:：]?|问题[:：]?|面试官问[:：]?|被问到[:：]?|追问[:：]?)", "", cleaned).strip()
        if cleaned and cleaned[-1] not in "?？。":
            cleaned += "？"
        return cleaned

    def _dedupe_questions(self, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        result = []
        for item in questions:
            key = normalize_skill(item["question"])[:80]
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _extract_rounds(self, text: str) -> list[str]:
        rounds = []
        for marker in ROUND_MARKERS:
            if marker in text and marker not in rounds:
                rounds.append(marker)
        return rounds

    def _line_round(self, line: str) -> str | None:
        for marker in ROUND_MARKERS:
            if marker in line:
                return marker
        return None

    def _extract_topics(self, text: str) -> list[str]:
        lowered = text.lower()
        topics = []
        for topic, aliases in TOPIC_ALIASES.items():
            if any(alias.lower() in lowered for alias in aliases):
                topics.append(topic)
        return topics

    def _credibility(
        self,
        *,
        source_site: str,
        source_url: str | None,
        text: str,
        question_count: int,
        topic_count: int,
    ) -> dict[str, Any]:
        known_site = source_site in set(KNOWN_SOURCE_SITES.values())
        has_url = bool(source_url)
        has_round = any(marker in text for marker in ROUND_MARKERS)
        score = 0.2
        if known_site:
            score += 0.2
        if has_url:
            score += 0.15
        if question_count >= 3:
            score += 0.25
        elif question_count:
            score += 0.12
        if topic_count >= 2:
            score += 0.1
        if has_round:
            score += 0.1
        noise_flags = []
        if len(text) < 80:
            noise_flags.append("text_too_short")
            score -= 0.1
        if question_count == 0:
            noise_flags.append("no_explicit_question")
            score -= 0.15
        return {
            "score": round(max(0.0, min(score, 1.0)), 4),
            "known_site": known_site,
            "has_url": has_url,
            "question_count": question_count,
            "topic_count": topic_count,
            "has_round_marker": has_round,
            "noise_flags": noise_flags,
        }

    def _relevance_score(self, row: InterviewExperience, job: Job, *, forced: bool) -> float:
        if forced:
            base = 2.0
        else:
            base = 0.0
        if row.job_id == job.id:
            base += 5.0
        if row.company and job.company and normalize_skill(row.company) == normalize_skill(job.company):
            base += 1.5
        role_text = " ".join([row.role_keyword or "", row.title or "", row.raw_text[:260]]).lower()
        job_title_tokens = [token for token in re.split(r"\s+", normalize_skill(job.title)) if len(token) >= 2]
        base += min(sum(1 for token in job_title_tokens if token in role_text), 3) * 0.4
        job_skills = self._job_skills(job)
        row_topics = {normalize_skill(topic) for topic in row.topics_json or []}
        overlap = {normalize_skill(skill) for skill in job_skills} & row_topics
        base += min(len(overlap), 5) * 0.5
        if (row.credibility_json or {}).get("question_count", 0) > 0:
            base += 0.8
        return round(base, 4)

    def _job_skills(self, job: Job) -> list[str]:
        structured = job.structured_jd_json or {}
        values = []
        for key in ("required_skills", "preferred_skills", "keywords"):
            values.extend(str(item) for item in structured.get(key, []) if str(item).strip())
        return values

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"[ \t]+", " ", str(text or "").replace("\u3000", " ")).strip()

    def _short_text(self, value: Any, limit: int = 120) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "..."
