import re

from app.core.config import get_settings
from app.core.llm import LLMClient
from app.core.llm import LLMConfigurationError
from app.core.llm import LLMResponseError
from app.core.llm import extract_json_object
from app.core.llm import format_exception
from app.models.schemas import JDStructured
from app.services.prompt_injection_guard import PromptInjectionGuard
from app.services.evidence_grounding import EvidenceGroundingService
from app.services.document_schema_batcher import DocumentSchemaBatcher
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
    "Prompt Engineering": [r"\bprompt engineering\b", "提示词工程"],
    "Prompt Regression": [r"\bprompt regression\b", "提示词回归"],
    "Prompt Injection": [r"\bprompt injection\b", "提示词注入"],
    "Model Evaluation": [
        r"\bmodel quality\b",
        r"\bmodel eval(uation)?\b",
        "模型质量",
        "模型评测",
        "模型评估",
        r"评测(?:经验|体系|系统|平台|指标|流程)",
        r"评估(?:经验|体系|系统|平台|指标|流程)",
    ],
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

JD_EXACT_SKILL_CANONICAL = {
    "智能体": "Agent",
    "大语言模型": "LLM",
    "大模型": "LLM",
    "向量数据库": "Vector Database",
    "向量检索": "Vector Database",
    "工具调用": "Tool Calling",
    "工作流": "Workflow",
    "安全护栏": "Guardrail",
    "安全策略": "Guardrail",
    "提示词工程": "Prompt Engineering",
    "提示词注入": "Prompt Injection",
    "模型评测": "Model Evaluation",
    "模型评估": "Model Evaluation",
    "模型质量评测": "Model Evaluation",
    "A/B实验": "A/B Testing",
    "A/B 实验": "A/B Testing",
    "AB实验": "A/B Testing",
    "AB 实验": "A/B Testing",
    "重排序": "Reranker",
    "检索增强生成": "RAG",
}

JD_JOB_TYPE_CANONICAL = {
    "intern": "internship",
    "internship": "internship",
    "实习": "internship",
    "实习生": "internship",
    "校招": "internship",
    "全职": "full-time",
    "兼职": "part-time",
    "远程": "remote",
}


class JDParserService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMClient()
        self.injection_guard = PromptInjectionGuard()
        self.grounding = EvidenceGroundingService()
        self.document_batcher = DocumentSchemaBatcher()

    async def parse_jd(
        self,
        raw_text: str,
        *,
        title: str | None = None,
        company: str | None = None,
        location: str | None = None,
        db=None,
    ) -> dict:
        safe_text, injection = self.injection_guard.sanitize_for_llm(raw_text, source="jd")
        heuristic = self.heuristic_parse(safe_text or raw_text, title=title, company=company, location=location)
        heuristic["prompt_injection"] = injection.model_dump()
        if not self.llm.available:
            if not self.settings.llm_fallback_enabled:
                raise LLMConfigurationError("LLM is required for JD parsing. Set LLM_FALLBACK_ENABLED=true for tests.")
            heuristic["quality_gate"] = self.grounding.evaluate_jd(
                raw_text,
                heuristic,
                allowed_values=self._grounding_allowed_values(
                    raw_text,
                    heuristic,
                    title=title,
                    company=company,
                    location=location,
                ),
            )
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
{safe_text or raw_text}
"""
        try:
            document_text = safe_text or raw_text
            chunks = (
                self.document_batcher.split(
                    document_text,
                    max_chars=self.settings.parser_document_batch_chars,
                )
                if self.settings.context_management_v3_enabled
                else [{"chunk_id": "document-1", "text": document_text}]
            )
            parsed_rows = []
            for index, chunk in enumerate(chunks, start=1):
                chunk_prompt = user_prompt.replace(
                    f"JD:\n{document_text}",
                    f"JD chunk {chunk['chunk_id']}:\n{chunk['text']}",
                )
                parsed_rows.append(
                    (
                        chunk,
                        await self._generate_jd_json_with_retry(
                            system_prompt=system_prompt,
                            user_prompt=chunk_prompt,
                            max_tokens=1200,
                            db=db,
                            trace_suffix=f"batch_{index}" if len(chunks) > 1 else None,
                        ),
                    )
                )
            parsed, parser_provenance = self.document_batcher.merge(
                parsed_rows,
                list_fields={
                    "required_skills",
                    "preferred_skills",
                    "responsibilities",
                    "qualifications",
                    "keywords",
                },
            )
            merged = self._merge_llm_parse(heuristic, parsed, raw_text=safe_text or raw_text)
            merged["prompt_injection"] = injection.model_dump()
            normalized = JDStructured.model_validate(merged).model_dump()
            normalized = self._canonicalize_structured_jd(normalized)
            normalized, rejected_optional_keywords = self._filter_unsupported_optional_keywords(
                normalized,
                raw_text=safe_text or raw_text,
            )
            quality_gate = self.grounding.evaluate_jd(
                raw_text,
                normalized,
                allowed_values=self._grounding_allowed_values(
                    raw_text,
                    normalized,
                    title=title,
                    company=company,
                    location=location,
                ),
            )
            quality_gate["rejected_optional_keywords"] = rejected_optional_keywords
            normalized["quality_gate"] = quality_gate
            normalized["parser_provenance"] = parser_provenance
            if not quality_gate["passed"]:
                raise LLMResponseError(
                    "JD parser quality gate rejected unsupported structured fields: "
                    f"skills={quality_gate['unsupported_skills'][:6]}, "
                    f"statements={quality_gate['unsupported_statements'][:3]}"
                )
            return normalized
        except Exception:
            if not self.settings.llm_fallback_enabled:
                raise
            heuristic["prompt_injection"] = self.injection_guard.detect(raw_text, source="jd").model_dump()
            heuristic["quality_gate"] = self.grounding.evaluate_jd(
                raw_text,
                heuristic,
                allowed_values=self._grounding_allowed_values(
                    raw_text,
                    heuristic,
                    title=title,
                    company=company,
                    location=location,
                ),
            )
            return heuristic

    def parse_jd_for_search(
        self,
        raw_text: str,
        *,
        title: str | None = None,
        company: str | None = None,
        location: str | None = None,
    ) -> dict:
        """Build a safe searchable JD without putting LLM latency on the result-list path."""
        safe_text, injection = self.injection_guard.sanitize_for_llm(raw_text, source="jd")
        parsed = self.heuristic_parse(
            safe_text or raw_text,
            title=title,
            company=company,
            location=location,
        )
        parsed["prompt_injection"] = injection.model_dump()
        parsed["quality_gate"] = self.grounding.evaluate_jd(
            raw_text,
            parsed,
            allowed_values=self._grounding_allowed_values(
                raw_text,
                parsed,
                title=title,
                company=company,
                location=location,
            ),
        )
        return JDStructured.model_validate(parsed).model_dump()

    def _grounding_allowed_values(
        self,
        raw_text: str,
        parsed: dict,
        *,
        title: str | None,
        company: str | None,
        location: str | None,
    ) -> list[str | None]:
        values: list[str | None] = [title, company, location]
        for field in ("required_skills", "preferred_skills", "keywords"):
            for value in parsed.get(field) or []:
                skill = str(value or "").strip()
                if skill and self._skill_mentioned(raw_text, skill):
                    values.append(skill)
        return values

    async def _generate_jd_json_with_retry(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        db=None,
        trace_suffix: str | None = None,
    ) -> dict:
        last_exc: Exception | None = None
        max_attempts = 3
        for attempt in range(max_attempts):
            trace_name = "jd_parser.parse_jd" if attempt == 0 else f"jd_parser.parse_jd.retry_{attempt}"
            if trace_suffix:
                trace_name = f"{trace_name}.{trace_suffix}"
            try:
                text = await self.llm.generate_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.1,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    db=db,
                    trace_name=trace_name,
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                message = format_exception(exc)
                if attempt >= max_attempts - 1 or not self._is_transient_llm_error(message):
                    raise
                continue

            try:
                return extract_json_object(text)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                message = format_exception(exc)
                if not self._is_repairable_json_error(message, text):
                    raise
                return await self._repair_jd_json(
                    user_prompt=user_prompt,
                    raw_text=text,
                    parse_error=message,
                    max_tokens=max_tokens,
                    db=db,
                )
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("JD parser LLM call did not return JSON.")

    async def _repair_jd_json(
        self,
        *,
        user_prompt: str,
        raw_text: str,
        parse_error: str,
        max_tokens: int,
        db=None,
    ) -> dict:
        repair_system = (
            "You repair a JD parsing response. Return one complete strict JSON object only. "
            "No Markdown, no explanation, no trailing text."
        )
        repair_prompt = (
            "The previous JD parsing response was invalid or truncated. Re-parse the original JD "
            "using the requested schema and return a complete JSON object with all required keys.\n\n"
            f"Parse error:\n{parse_error}\n\n"
            f"Invalid response preview:\n{raw_text[:2000]}\n\n"
            f"Original parsing request:\n{user_prompt}"
        )
        repaired_text = await self.llm.generate_text(
            system_prompt=repair_system,
            user_prompt=repair_prompt,
            temperature=0,
            max_tokens=max(max_tokens, 1600),
            response_format={"type": "json_object"},
            db=db,
            trace_name="jd_parser.parse_jd.repair_json",
        )
        return extract_json_object(repaired_text)

    def _is_repairable_json_error(self, message: str, text: str) -> bool:
        if not text.strip():
            return False
        repairable_terms = [
            "json",
            "did not contain a json object",
            "unterminated string",
            "expecting",
            "extra data",
        ]
        lowered = message.lower()
        return any(term in lowered for term in repairable_terms)

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

    def _merge_llm_parse(self, heuristic: dict, parsed: dict, *, raw_text: str | None = None) -> dict:
        merged = {**heuristic, **parsed}
        for field in ["required_skills", "preferred_skills", "responsibilities", "qualifications", "keywords"]:
            merged[field] = self._merge_ordered_lists(parsed.get(field), heuristic.get(field))
        for field in ["title", "company", "location", "job_type", "seniority"]:
            merged[field] = parsed.get(field) or heuristic.get(field)
        normalized = self._normalize_requirement_strength(merged, heuristic=heuristic, raw_text=raw_text)
        return self._canonicalize_structured_jd(normalized)

    def _canonicalize_structured_jd(self, parsed: dict) -> dict:
        output = dict(parsed)

        def canonicalize_skills(values: object) -> list[str]:
            items = values if isinstance(values, list) else ([] if values is None else [values])
            canonical: list[str] = []
            seen: set[str] = set()
            for item in items:
                value = str(item).strip()
                normalized_value = re.sub(r"\s+", " ", value.lower())
                mapped = JD_EXACT_SKILL_CANONICAL.get(value, value)
                mapped = JD_EXACT_SKILL_CANONICAL.get(normalized_value, mapped)
                key = mapped.lower()
                if mapped and key not in seen:
                    seen.add(key)
                    canonical.append(mapped)
            return canonical

        required = canonicalize_skills(output.get("required_skills"))
        preferred = canonicalize_skills(output.get("preferred_skills"))
        required_keys = {item.lower() for item in required}
        output["required_skills"] = required
        output["preferred_skills"] = [item for item in preferred if item.lower() not in required_keys]
        raw_job_type = str(output.get("job_type") or "").strip()
        output["job_type"] = JD_JOB_TYPE_CANONICAL.get(raw_job_type.lower(), raw_job_type or None)
        if output["job_type"] == "internship" and not output.get("seniority"):
            output["seniority"] = "intern"
        return output

    def _filter_unsupported_optional_keywords(
        self,
        parsed: dict,
        *,
        raw_text: str,
    ) -> tuple[dict, list[str]]:
        output = dict(parsed)
        supported: list[str] = []
        rejected: list[str] = []
        for item in output.get("keywords") or []:
            value = str(item).strip()
            if not value:
                continue
            if self.grounding.value_supported(value, raw_text):
                supported.append(value)
            else:
                rejected.append(value)
        output["keywords"] = supported
        return output, rejected

    def _normalize_requirement_strength(
        self,
        merged: dict,
        *,
        heuristic: dict,
        raw_text: str | None,
    ) -> dict:
        required = [str(item).strip() for item in merged.get("required_skills") or [] if str(item).strip()]
        preferred = [str(item).strip() for item in merged.get("preferred_skills") or [] if str(item).strip()]
        heuristic_required = {self._skill_key(item) for item in heuristic.get("required_skills") or []}
        heuristic_preferred = {self._skill_key(item) for item in heuristic.get("preferred_skills") or []}
        preferred_keys = {self._skill_key(item) for item in preferred}

        normalized_required: list[str] = []
        demoted: list[str] = []
        for skill in required:
            key = self._skill_key(skill)
            heuristic_says_preferred_only = key in heuristic_preferred and key not in heuristic_required
            raw_says_soft_only = self._skill_is_soft_requirement_only(skill, raw_text or "")
            duplicated_as_preferred = key in preferred_keys
            if heuristic_says_preferred_only or raw_says_soft_only or (duplicated_as_preferred and key not in heuristic_required):
                demoted.append(skill)
                continue
            normalized_required.append(skill)

        merged["required_skills"] = self._merge_ordered_lists(normalized_required, [])
        merged["preferred_skills"] = self._merge_ordered_lists(preferred, demoted)
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

    def _skill_key(self, skill: object) -> str:
        return re.sub(r"\s+", " ", str(skill).strip().lower())

    def _skill_is_soft_requirement_only(self, skill: str, raw_text: str) -> bool:
        if not raw_text.strip():
            return False
        hard = False
        soft = False
        for sentence in self._sentences_with_skill(raw_text, skill):
            lowered = sentence.lower()
            has_soft = self._has_soft_requirement_cue(lowered)
            has_hard = self._has_hard_requirement_cue(lowered)
            if has_soft:
                soft = True
            if has_hard and not has_soft:
                hard = True
        return soft and not hard

    def _sentences_with_skill(self, raw_text: str, skill: str) -> list[str]:
        sentences = [
            segment.strip()
            for segment in re.split(r"[\n。；;.!?？]+", raw_text)
            if segment.strip()
        ]
        return [sentence for sentence in sentences if self._skill_mentioned(sentence, skill)]

    def _skill_mentioned(self, text: str, skill: str) -> bool:
        patterns = [self._skill_pattern(skill)]
        patterns.extend(JD_SKILL_ALIASES.get(skill, []))
        canonical = next((name for name in JD_SKILL_ALIASES if name.lower() == skill.lower()), None)
        if canonical and canonical != skill:
            patterns.extend(JD_SKILL_ALIASES.get(canonical, []))
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)

    def _has_soft_requirement_cue(self, text: str) -> bool:
        soft_tokens = [
            "preferred",
            "nice to have",
            "bonus",
            "plus",
            "optional",
            "helpful",
            "not required",
            "not mandatory",
            "加分",
            "优先",
            "非必须",
            "非硬性",
            "不是硬性要求",
            "可选",
            "了解即可",
            "有经验者优先",
        ]
        return any(token in text for token in soft_tokens)

    def _has_hard_requirement_cue(self, text: str) -> bool:
        hard_tokens = [
            "require",
            "required",
            "must",
            "need",
            "proficient",
            "hands-on",
            "experience with",
            "build",
            "develop",
            "implement",
            "必须",
            "必备",
            "需要",
            "要求",
            "熟悉",
            "熟练",
            "掌握",
            "负责",
            "参与",
            "开发",
            "实现",
            "构建",
        ]
        return any(token in text for token in hard_tokens)

    def heuristic_parse(
        self,
        raw_text: str,
        *,
        title: str | None = None,
        company: str | None = None,
        location: str | None = None,
    ) -> dict:
        lines = [
            segment.strip(" -•\t")
            for line in raw_text.splitlines()
            for segment in re.split(r"(?<=[。；;])\s*", line)
            if segment.strip(" -•\t")
        ]
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
            "负责",
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
        header_pattern = (
            r"^(?:岗位职责|工作职责|工作内容|主要职责|职责描述|负责|"
            r"任职要求|任职资格|岗位要求|基本要求|要求|"
            r"responsibilities?|qualifications?|requirements?)\s*"
        )
        content = re.sub(header_pattern, "", line, count=1, flags=re.IGNORECASE).strip(" -•\t")
        if content != line.strip(" -•\t"):
            return content
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
