from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Iterable

from app.services.vector_index import tokenize


NEGATIVE_OR_WEAK_CUES = (
    "did not",
    "does not",
    "do not",
    "not implement",
    "not build",
    "no shipped",
    "no direct",
    "no experience",
    "without experience",
    "planned learning",
    "plans to learn",
    "currently learning",
    "coursework only",
    "read articles",
    "read papers",
    "没有",
    "未实现",
    "未交付",
    "无相关经验",
    "计划学习",
    "正在学习",
    "课程作业",
    "仅了解",
)

CLAIM_CUES = (
    "built",
    "implemented",
    "created",
    "designed",
    "developed",
    "delivered",
    "maintained",
    "owned",
    "led",
    "responsible for",
    "experience",
    "familiar with",
    "proficient",
    "worked on",
    "improved",
    "reduced",
    "increased",
    "构建",
    "实现",
    "开发了",
    "开发并",
    "交付",
    "维护",
    "负责",
    "主导",
    "搭建",
    "落地",
    "完成",
    "做过",
    "参与",
    "优化",
    "提升",
    "降低",
)

TERM_ALIASES: dict[str, tuple[str, ...]] = {
    "agent": ("智能体", "agentic"),
    "rag": ("retrieval augmented generation", "检索增强", "知识库问答"),
    "llm": ("large language model", "大语言模型", "大模型"),
    "evaluation": ("model evaluation", "模型评测", "评测", "评估"),
    "model evaluation": ("evaluation", "模型评测", "模型评估", "评测", "评估"),
    "a b testing": (
        "a/b test",
        "ab testing",
        "a b testing",
        "a/b实验",
        "a/b 实验",
        "ab实验",
        "ab 实验",
        "对照实验",
    ),
    "recommendation": ("recommendation system", "推荐系统", "推荐算法", "推荐"),
    "ranking": ("ranker", "reranking", "排序模型", "召回排序", "排序"),
    "prompt injection": ("提示词注入", "提示注入", "指令注入"),
    "guardrail": ("guardrails", "安全护栏", "安全校验", "风控"),
    "vector database": ("vector store", "vector index", "向量数据库", "向量索引", "向量检索"),
    "tool calling": ("function calling", "工具调用"),
    "prompt engineering": ("提示词工程", "提示词"),
    "workflow": ("workflows", "orchestration", "工作流", "编排"),
    "reranker": ("reranking", "cross encoder", "cross-encoder", "重排序"),
}


class EvidenceGroundingService:
    """High-precision lexical grounding shared by parsers and generated artifacts."""

    def evaluate_resume(self, raw_text: str, parsed: dict[str, Any]) -> dict[str, Any]:
        unsupported_fields: list[dict[str, str]] = []
        evaluated = 0
        supported = 0

        def check(field: str, value: Any, *, positive: bool = False) -> None:
            nonlocal evaluated, supported
            clean = str(value or "").strip()
            if not clean:
                return
            evaluated += 1
            is_supported = (
                self.has_positive_support(clean, raw_text)
                if positive
                else self.value_supported(clean, raw_text)
            )
            if is_supported:
                supported += 1
            else:
                unsupported_fields.append({"field": field, "value": clean})

        def check_statement(field: str, value: Any) -> None:
            nonlocal evaluated, supported
            clean = str(value or "").strip()
            if not clean:
                return
            evaluated += 1
            score = self.support_score(clean, raw_text)
            if score >= 0.35:
                supported += 1
            else:
                unsupported_fields.append({"field": field, "value": clean})

        for field in ("name", "email", "phone", "location", "availability", "headline"):
            check(field, parsed.get(field))
        check_statement("self_summary", parsed.get("self_summary"))
        for index, role in enumerate(parsed.get("target_roles") or []):
            check(f"target_roles[{index}]", role)
        for index, skill in enumerate(parsed.get("skills") or []):
            check(f"skills[{index}]", skill, positive=True)
        for index, project in enumerate(parsed.get("projects") or []):
            if not isinstance(project, dict):
                continue
            check(f"projects[{index}].name", project.get("name"))
            check_statement(f"projects[{index}].description", project.get("description"))
            check_statement(f"projects[{index}].impact", project.get("impact"))
            for skill_index, skill in enumerate(project.get("tech_stack") or []):
                check(f"projects[{index}].tech_stack[{skill_index}]", skill, positive=True)
        for section in ("work_experience", "campus_experience"):
            for index, experience in enumerate(parsed.get(section) or []):
                if not isinstance(experience, dict):
                    continue
                for field in ("company", "role", "duration"):
                    check(f"{section}[{index}].{field}", experience.get(field))
                check_statement(f"{section}[{index}].details", experience.get("details"))
                for skill_index, skill in enumerate(experience.get("tech_stack") or []):
                    check(f"{section}[{index}].tech_stack[{skill_index}]", skill, positive=True)
        for index, education in enumerate(parsed.get("education") or []):
            if not isinstance(education, dict):
                continue
            for field in ("school", "degree", "major", "duration"):
                check(f"education[{index}].{field}", education.get(field))
            check_statement(f"education[{index}].details", education.get("details"))
        for field in ("certifications", "awards", "languages", "portfolio_links"):
            for index, value in enumerate(parsed.get(field) or []):
                check(f"{field}[{index}]", value)

        unsupported_critical = [
            item
            for item in unsupported_fields
            if item["field"] in {"name", "email", "phone"}
        ]
        unsupported_skills = [item for item in unsupported_fields if "skill" in item["field"]]
        unsupported_claim_fields = [
            item
            for item in unsupported_fields
            if any(token in item["field"] for token in (".description", ".impact", ".details"))
        ]
        grounding_rate = round(supported / max(evaluated, 1), 4)
        passed = (
            not unsupported_critical
            and not unsupported_skills
            and not unsupported_claim_fields
            and grounding_rate >= 0.9
        )
        return {
            "passed": passed,
            "grounding_rate": grounding_rate,
            "evaluated_field_count": evaluated,
            "supported_field_count": supported,
            "unsupported_field_count": len(unsupported_fields),
            "unsupported_fields": unsupported_fields[:20],
            "unsupported_critical_fields": unsupported_critical,
            "unsupported_skills": unsupported_skills,
            "unsupported_claim_fields": unsupported_claim_fields,
        }

    def evaluate_jd(
        self,
        raw_text: str,
        parsed: dict[str, Any],
        *,
        allowed_values: Iterable[str | None] = (),
    ) -> dict[str, Any]:
        allowed = {self.normalize(value) for value in allowed_values if str(value or "").strip()}
        unsupported_skills: list[dict[str, str]] = []
        for field in ("required_skills", "preferred_skills", "keywords"):
            for value in parsed.get(field) or []:
                clean = str(value or "").strip()
                if clean and not self.value_supported(clean, raw_text):
                    unsupported_skills.append({"field": field, "value": clean})

        statement_results: list[dict[str, Any]] = []
        for field in ("responsibilities", "qualifications"):
            for value in parsed.get(field) or []:
                clean = str(value or "").strip()
                if not clean:
                    continue
                score = self.support_score(clean, raw_text)
                statement_results.append(
                    {"field": field, "value": clean[:240], "score": score, "supported": score >= 0.35}
                )

        unsupported_metadata = []
        for field in ("title", "company", "location"):
            value = str(parsed.get(field) or "").strip()
            if not value or self.normalize(value) in allowed or self.value_supported(value, raw_text):
                continue
            unsupported_metadata.append({"field": field, "value": value})

        supported_statements = sum(1 for item in statement_results if item["supported"])
        statement_grounding_rate = (
            round(supported_statements / len(statement_results), 4) if statement_results else 1.0
        )
        passed = not unsupported_skills and not unsupported_metadata and statement_grounding_rate >= 0.8
        return {
            "passed": passed,
            "statement_grounding_rate": statement_grounding_rate,
            "statement_count": len(statement_results),
            "unsupported_statement_count": sum(1 for item in statement_results if not item["supported"]),
            "unsupported_statements": [item for item in statement_results if not item["supported"]][:12],
            "unsupported_skills": unsupported_skills,
            "unsupported_metadata": unsupported_metadata,
        }

    def evaluate_citations(
        self,
        citations: Iterable[Any],
        sources: Iterable[Any],
        *,
        threshold: float = 0.58,
        require_positive: bool = True,
    ) -> dict[str, Any]:
        source_texts = [str(item or "").strip() for item in sources if str(item or "").strip()]
        results = []
        for raw in citations:
            citation = str(raw or "").strip()
            if not citation:
                continue
            best = max((self.support_score(citation, source) for source in source_texts), default=0.0)
            positive = any(self.has_positive_support(citation, source) for source in source_texts)
            supported = best >= threshold and (positive or not require_positive)
            results.append(
                {
                    "citation": citation[:300],
                    "support_score": best,
                    "positive_support": positive,
                    "supported": supported,
                }
            )
        supported_count = sum(1 for item in results if item["supported"])
        return {
            "passed": bool(results) and supported_count == len(results),
            "grounding_rate": round(supported_count / max(len(results), 1), 4),
            "citation_count": len(results),
            "unsupported_citations": [item for item in results if not item["supported"]],
            "results": results,
        }

    def evaluate_generated_claims(
        self,
        text: str,
        sources: Iterable[Any],
        *,
        threshold: float = 0.34,
    ) -> dict[str, Any]:
        source_text = "\n".join(str(item or "") for item in sources if str(item or "").strip())
        claims = self.extract_candidate_claims(text)
        results = []
        for claim in claims:
            score = self.support_score(claim, source_text)
            results.append(
                {
                    "claim": claim[:300],
                    "support_score": score,
                    "supported": score >= threshold,
                }
            )
        unsupported = [item for item in results if not item["supported"]]
        return {
            "passed": not unsupported,
            "claim_count": len(results),
            "supported_claim_count": len(results) - len(unsupported),
            "grounding_rate": round((len(results) - len(unsupported)) / max(len(results), 1), 4),
            "unsupported_claims": unsupported,
            "results": results,
        }

    def unsupported_numbers(self, text: str, sources: Iterable[Any]) -> list[str]:
        source_text = "\n".join(str(item or "") for item in sources if str(item or "").strip())
        source_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", source_text))
        return sorted(
            number
            for number in set(re.findall(r"\b\d+(?:\.\d+)?%?\b", text or ""))
            if number not in source_numbers
        )

    def extract_candidate_claims(self, text: str) -> list[str]:
        claims: list[str] = []
        for line in re.split(r"[\n。！？!?；;]+", text or ""):
            clean = re.sub(r"^[\s#>*\-\d.、]+", "", line).strip()
            lowered = clean.lower()
            if len(clean) < 12 or not any(cue in lowered for cue in CLAIM_CUES):
                continue
            claims.append(clean)
        return list(dict.fromkeys(claims))

    def has_positive_support(self, value: str, source: str) -> bool:
        candidates = self._term_candidates(value)
        for sentence in self.sentences(source):
            sentence_normalized = self.normalize(sentence)
            if not any(
                candidate in sentence_normalized or self.support_score(candidate, sentence) >= 0.78
                for candidate in candidates
            ):
                continue
            lowered = sentence.lower()
            if not any(cue in lowered for cue in NEGATIVE_OR_WEAK_CUES):
                return True
        return False

    def value_supported(self, value: str, source: str) -> bool:
        source_normalized = self.normalize(source)
        return any(
            candidate in source_normalized or self.support_score(candidate, source) >= 0.78
            for candidate in self._term_candidates(value)
            if candidate
        )

    def support_score(self, claim: str, source: str) -> float:
        normalized_claim = self.normalize(claim)
        normalized_source = self.normalize(source)
        if not normalized_claim or not normalized_source:
            return 0.0
        if normalized_claim in normalized_source:
            return 1.0
        claim_tokens = {token for token in tokenize(normalized_claim) if len(token) > 1}
        source_tokens = {token for token in tokenize(normalized_source) if len(token) > 1}
        token_overlap = len(claim_tokens & source_tokens) / max(len(claim_tokens), 1)
        longest = SequenceMatcher(None, normalized_claim, normalized_source).find_longest_match()
        sequence_coverage = longest.size / max(len(normalized_claim), 1)
        return round(max(token_overlap, sequence_coverage), 4)

    def _term_candidates(self, value: str) -> list[str]:
        normalized = self.normalize(value)
        aliases = TERM_ALIASES.get(normalized, ())
        singular = normalized[:-1] if normalized.endswith("s") else normalized
        return list(dict.fromkeys([normalized, singular, *(self.normalize(alias) for alias in aliases)]))

    @staticmethod
    def sentences(text: str) -> list[str]:
        return [item.strip() for item in re.split(r"[\n。！？!?；;]+", text or "") if item.strip()]

    @staticmethod
    def normalize(value: Any) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff+#.]+", " ", str(value or "").lower()).strip()
