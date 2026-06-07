import re

from app.core.config import get_settings
from app.core.llm import LLMClient
from app.core.llm import LLMConfigurationError
from app.models.schemas import JDStructured
from app.services.resume_parser import KNOWN_SKILLS


JD_SKILL_ALIASES: dict[str, list[str]] = {
    "Agent": [r"\bai agents?\b", r"\bagentic\b", "智能体"],
    "RAG": [r"\bretrieval[- ]augmented generation\b", r"\brag\b", "检索增强", "知识库问答"],
    "LLM": [r"\blarge language models?\b", r"\bllms?\b", "大语言模型", "大模型"],
    "Vector Database": [
        r"\bvector (database|store|index|search)\b",
        "向量数据库",
        "向量检索",
        "向量索引",
    ],
    "Embedding": [r"\bembeddings?\b", "嵌入模型", "语义向量"],
    "Reranker": [r"\brerank(er|ing)?\b", r"\bcross[- ]encoder\b", "重排序"],
    "Tool Calling": [r"\btool calling\b", r"\bfunction calling\b", "工具调用"],
    "Workflow": [r"\bworkflows?\b", r"\borchestration\b", "工作流", "编排"],
    "Guardrail": [r"\bguardrails?\b", "安全护栏", "风控策略", "安全策略"],
    "Prompt Engineering": [r"\bprompt engineering\b", r"\bprompts?\b", "提示词工程", "提示词"],
    "Prompt Regression": [r"\bprompt regression\b", "提示词回归"],
    "Prompt Injection": [r"\bprompt injection\b", "提示词注入"],
    "Model Evaluation": [r"\bmodel quality\b", r"\bmodel eval(uation)?\b", "模型质量", "模型评测"],
    "A/B Testing": [r"\ba/b tests?\b", r"\ba/b testing\b", r"\bab tests?\b", "A/B实验", "AB实验"],
    "Feature Store": [r"\bfeature stores?\b", "特征平台", "特征库"],
    "MLflow": [r"\bmlflow\b"],
    "Airflow": [r"\bairflow\b"],
    "Spark": [r"\bspark\b"],
    "Kafka": [r"\bkafka\b"],
    "Recommendation": [r"\brecommendation(s)?\b", "推荐系统", "推荐算法"],
    "Ranking": [r"\branking\b", "排序模型", "召回排序"],
    "CTR": [r"\bctr\b", "点击率"],
    "MLOps": [r"\bmlops\b", "模型部署", "模型上线"],
    "OpenCV": [r"\bopencv\b"],
    "Computer Vision": [r"\bcomputer vision\b", "计算机视觉", "图像识别"],
}


class JDParserService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMClient()

    async def parse_jd(
        self,
        raw_text: str,
        *,
        title: str | None = None,
        company: str | None = None,
        location: str | None = None,
        db=None,
    ) -> dict:
        heuristic = self.heuristic_parse(raw_text, title=title, company=company, location=location)
        if not self.llm.available:
            if not self.settings.llm_fallback_enabled:
                raise LLMConfigurationError("LLM is required for JD parsing. Set LLM_FALLBACK_ENABLED=true for tests.")
            return heuristic

        system_prompt = "You parse job descriptions. Return strict JSON only."
        user_prompt = f"""
Parse this job description into JSON:
{{
  "title": string|null,
  "company": string|null,
  "location": string|null,
  "job_type": string|null,
  "required_skills": [string],
  "preferred_skills": [string],
  "responsibilities": [string],
  "qualifications": [string],
  "keywords": [string],
  "seniority": string|null
}}

Known title/company/location if provided:
- title: {title}
- company: {company}
- location: {location}

JD:
{raw_text}
"""
        try:
            parsed = await self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                db=db,
                trace_name="jd_parser.parse_jd",
            )
            return JDStructured.model_validate(self._merge_llm_parse(heuristic, parsed)).model_dump()
        except Exception:
            if not self.settings.llm_fallback_enabled:
                raise
            return heuristic

    def _merge_llm_parse(self, heuristic: dict, parsed: dict) -> dict:
        merged = {**heuristic, **parsed}
        for field in ["required_skills", "preferred_skills", "responsibilities", "qualifications", "keywords"]:
            merged[field] = self._merge_ordered_lists(parsed.get(field), heuristic.get(field))
        for field in ["title", "company", "location", "job_type", "seniority"]:
            merged[field] = parsed.get(field) or heuristic.get(field)
        return merged

    def _merge_ordered_lists(self, primary: object, secondary: object) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for source in [primary, secondary]:
            items = source if isinstance(source, list) else ([] if source is None else [source])
            for item in items:
                value = str(item).strip()
                key = value.lower()
                if value and key not in seen:
                    seen.add(key)
                    merged.append(value)
        return merged

    def heuristic_parse(
        self,
        raw_text: str,
        *,
        title: str | None = None,
        company: str | None = None,
        location: str | None = None,
    ) -> dict:
        lines = [line.strip(" -•\t") for line in raw_text.splitlines() if line.strip(" -•\t")]
        guessed_title = title or self._guess_title(lines)
        responsibilities, qualifications, preferred_lines = self._split_responsibilities(lines)
        skill_text = "\n".join(responsibilities + qualifications) or raw_text
        skills = self._extract_skills(skill_text)
        preferred_skills = [
            skill
            for skill in self._extract_skills("\n".join(preferred_lines), ignore_negation=True)
            if skill not in set(skills)
        ]
        keywords = sorted(set(skills + preferred_skills + self._keyword_phrases(raw_text)))[:40]
        job_type = self._guess_job_type(raw_text, guessed_title, location)
        return JDStructured(
            title=guessed_title,
            company=company,
            location=location,
            job_type=job_type,
            required_skills=skills[:24],
            preferred_skills=preferred_skills[:12],
            responsibilities=responsibilities[:12],
            qualifications=qualifications[:12],
            keywords=keywords,
            seniority="intern" if job_type and "intern" in job_type.lower() else None,
        ).model_dump()

    def _guess_title(self, lines: list[str]) -> str | None:
        for line in lines[:6]:
            if 4 <= len(line) <= 120:
                return line
        return None

    def _extract_skills(self, text: str, *, ignore_negation: bool = False) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()

        def add(skill: str) -> None:
            key = skill.lower()
            if key not in seen:
                seen.add(key)
                found.append(skill)

        for skill in KNOWN_SKILLS:
            if self._pattern_found(text, self._skill_pattern(skill), ignore_negation=ignore_negation):
                add(skill)
        for canonical, patterns in JD_SKILL_ALIASES.items():
            if any(self._pattern_found(text, pattern, ignore_negation=ignore_negation) for pattern in patterns):
                add(canonical)
        return found

    def _skill_pattern(self, skill: str) -> str:
        escaped = re.escape(skill)
        return rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"

    def _pattern_found(self, text: str, pattern: str, *, ignore_negation: bool = False) -> bool:
        flags = re.IGNORECASE
        for match in re.finditer(pattern, text, flags):
            if ignore_negation or not self._match_is_negated(text, match.start(), match.end()):
                return True
        return False

    def _match_is_negated(self, text: str, start: int, end: int) -> bool:
        window = self._match_sentence(text, start, end).lower()
        negation_patterns = [
            r"\bno\s+(prior\s+)?[^.\n;:]{0,50}\b(required|needed|mandatory)\b",
            r"\bnot\s+[^.\n;:]{0,50}\b(required|needed|mandatory|necessary)\b",
            r"\bwithout\s+requiring\b",
            "不要求",
            "不需要",
            "无需",
            "非必须",
            "可不具备",
        ]
        return any(re.search(pattern, window) for pattern in negation_patterns)

    def _match_sentence(self, text: str, start: int, end: int) -> str:
        left_boundaries = [text.rfind(token, 0, start) for token in ["\n", ".", ";", "；"]]
        line_start = max(left_boundaries) + 1
        right_boundaries = [text.find(token, end) for token in ["\n", ".", ";", "；"]]
        positive_right_boundaries = [index for index in right_boundaries if index >= 0]
        line_end = min(positive_right_boundaries) if positive_right_boundaries else len(text)
        return text[line_start:line_end]

    def _split_responsibilities(self, lines: list[str]) -> tuple[list[str], list[str], list[str]]:
        responsibilities: list[str] = []
        qualifications: list[str] = []
        preferred: list[str] = []
        mode = "responsibility"
        qualification_tokens = [
            "qualification",
            "requirement",
            "must have",
            "what you bring",
            "任职",
            "要求",
            "岗位要求",
            "基本要求",
        ]
        responsibility_tokens = [
            "responsibil",
            "what you",
            "you will",
            "岗位职责",
            "工作职责",
            "工作内容",
            "主要职责",
            "职责描述",
        ]
        preferred_tokens = ["preferred", "nice to have", "bonus", "plus", "optional", "加分", "优先", "非必须"]
        for line in lines:
            lowered = line.lower()
            if any(token in lowered for token in preferred_tokens):
                mode = "preferred"
                content = self._content_after_header(line)
                if len(content) >= 8:
                    preferred.append(content)
                continue
            if any(token in lowered for token in qualification_tokens):
                mode = "qualification"
                content = self._content_after_header(line)
                if len(content) >= 8:
                    qualifications.append(content)
                continue
            if any(token in lowered for token in responsibility_tokens):
                mode = "responsibility"
                content = self._content_after_header(line)
                if len(content) >= 8:
                    responsibilities.append(content)
                continue
            if len(line) < 8:
                continue
            if mode == "preferred":
                preferred.append(line)
            elif mode == "qualification":
                qualifications.append(line)
            else:
                responsibilities.append(line)
        return responsibilities, qualifications, preferred

    def _content_after_header(self, line: str) -> str:
        parts = re.split(r"[:：]", line, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip(" -•\t")
        return ""

    def _keyword_phrases(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-\+#\.]{2,}", text)
        stop = {"and", "the", "with", "for", "you", "our", "are", "will", "this", "that"}
        return [token for token in tokens if token.lower() not in stop and len(token) <= 24]

    def _guess_job_type(self, text: str, title: str | None, location: str | None = None) -> str | None:
        haystack = f"{title or ''}\n{location or ''}\n{text}".lower()
        if re.search(r"(?<![a-z])intern(ship)?(?![a-z])", haystack) or any(
            token in haystack for token in ["实习", "校招"]
        ):
            return "internship"
        if "remote" in haystack or "远程" in haystack:
            return "remote"
        if "part-time" in haystack or "兼职" in haystack:
            return "part-time"
        if "full-time" in haystack or "full time" in haystack or "全职" in haystack:
            return "full-time"
        if re.search(r"\b(engineer|developer|analyst|scientist|architect)\b", haystack) or any(
            token in haystack for token in ["工程师", "开发", "算法"]
        ):
            return "full-time"
        return None
