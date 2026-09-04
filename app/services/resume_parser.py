import asyncio
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMClient
from app.core.llm import LLMConfigurationError
from app.core.llm import LLMResponseError
from app.core.llm import extract_json_object
from app.core.llm import format_exception
from app.models.entities import Profile
from app.models.schemas import GuidedProfileRequest, ProfileStructured
from app.services.prompt_injection_guard import PromptInjectionGuard
from app.services.evidence_grounding import EvidenceGroundingService
from app.services.document_schema_batcher import DocumentSchemaBatcher
from app.services.pdf_extraction import PDFExtractionService
from app.services.text_splitter import PDFPageText, ResumeTextSplitter
from app.services.vector_index import SQLiteVectorIndex


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-()]{7,}\d)")

KNOWN_SKILLS = [
    "Python",
    "FastAPI",
    "LangChain",
    "LangGraph",
    "RAG",
    "Agent",
    "LLM",
    "OpenAI",
    "DeepSeek",
    "SQL",
    "SQLite",
    "PostgreSQL",
    "Redis",
    "Docker",
    "Kubernetes",
    "React",
    "TypeScript",
    "JavaScript",
    "Node.js",
    "Pydantic",
    "SQLAlchemy",
    "PyTorch",
    "TensorFlow",
    "Transformers",
    "Chroma",
    "FAISS",
    "Playwright",
    "MCP",
    "Prompt Engineering",
    "Evaluation",
]


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("._")
    return cleaned or "resume.pdf"


def extract_pdf_text(path: Path) -> str:
    return "\n".join(page.text for page in extract_pdf_pages(path)).strip()


def extract_pdf_pages(path: Path) -> list[PDFPageText]:
    return PDFExtractionService().extract(filename=path.name, file_bytes=path.read_bytes()).pages


class ResumeParserService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMClient()
        self.splitter = ResumeTextSplitter(
            self.settings.chunk_size,
            self.settings.chunk_overlap,
            self.settings.pdf_cross_page_tail_chars,
            self.settings.pdf_cross_page_head_chars,
        )
        self.vector_index = SQLiteVectorIndex()
        self.injection_guard = PromptInjectionGuard()
        self.grounding = EvidenceGroundingService()
        self.pdf_extraction = PDFExtractionService(settings=self.settings)
        self.document_batcher = DocumentSchemaBatcher()
        self.settings.upload_path.mkdir(parents=True, exist_ok=True)

    async def create_profile_from_pdf(self, db: Session, *, filename: str, file_bytes: bytes) -> Profile:
        extraction = await asyncio.to_thread(
            self.pdf_extraction.extract,
            filename=filename,
            file_bytes=file_bytes,
        )
        path = self.settings.upload_path / f"{uuid4().hex}_{safe_filename(filename)}"
        path.write_bytes(file_bytes)
        pages = extraction.pages
        raw_text = extraction.raw_text
        structured = await self.parse_structured_resume(raw_text, db=db)
        structured["source_diagnostics"] = {"pdf_extraction": extraction.as_dict()}
        return self._create_profile(db, structured=structured, source_type="pdf", pages=pages)

    def create_profile_from_guided_answers(self, db: Session, payload: GuidedProfileRequest) -> Profile:
        structured = ProfileStructured(
            name=payload.name,
            email=payload.email,
            phone=payload.phone,
            photo_data_url=payload.photo_data_url,
            location=payload.location,
            availability=payload.availability,
            headline=payload.headline,
            self_summary=payload.self_summary,
            enabled_sections=payload.enabled_sections,
            target_roles=payload.target_roles,
            education=payload.education,
            skills=payload.skills,
            projects=payload.projects,
            work_experience=payload.work_experience,
            campus_experience=payload.campus_experience,
            certifications=payload.certifications,
            awards=payload.awards,
            languages=payload.languages,
            portfolio_links=payload.portfolio_links,
            prompt_injection=self.injection_guard.detect(self._guided_payload_to_text(payload), source="guided_resume").model_dump(),
            raw_text=self._guided_payload_to_text(payload),
        ).model_dump()
        return self._create_profile(db, structured=structured, source_type="guided")

    async def parse_structured_resume(self, raw_text: str, db=None) -> dict:
        safe_text, injection = self.injection_guard.sanitize_for_llm(raw_text, source="resume_pdf")
        grounding_source = safe_text or raw_text
        heuristic = self._heuristic_parse(grounding_source)
        heuristic["prompt_injection"] = injection.model_dump()
        if not self.llm.available:
            if not self.settings.llm_fallback_enabled:
                raise LLMConfigurationError(
                    "LLM is required for resume parsing. Set LLM_FALLBACK_ENABLED=true for tests."
                )
            heuristic["quality_gate"] = self.grounding.evaluate_resume(grounding_source, heuristic)
            return heuristic

        system_prompt = (
            "You are a careful resume parser. Return strict JSON only. "
            "Never infer unsupported facts. A project name or technology mention is not a target role. "
            "Only populate target_roles when the resume explicitly states a job objective, desired role, "
            "target role, or candidate headline. Planned learning and reading do not count as skills."
        )
        user_prompt = f"""
Parse the resume into this JSON schema:
{{
  "name": string|null,
  "email": string|null,
  "phone": string|null,
  "photo_data_url": null,
  "location": string|null,
  "availability": string|null,
  "headline": string|null,
  "self_summary": string|null,
  "enabled_sections": [string],
  "target_roles": [string],
  "education": [{{"school": string, "degree": string, "major": string, "duration": string, "details": string}}],
  "skills": [string],
  "projects": [{{"name": string, "description": string, "tech_stack": [string], "impact": string}}],
  "work_experience": [{{"company": string, "role": string, "duration": string, "details": string, "tech_stack": [string]}}],
  "campus_experience": [{{"company": string, "role": string, "duration": string, "details": string, "tech_stack": [string]}}],
  "certifications": [string],
  "awards": [string],
  "languages": [string],
  "portfolio_links": [string]
}}

Rules:
- Use null or [] when unknown.
- Keep all facts grounded in the original text.
- target_roles must be [] unless the resume explicitly states a desired role or candidate headline.
- Exclude skills that only occur in negated experience, reading, coursework-only, current-learning, or future plans.
- Do not include raw_text in the JSON output. The service will store the original text separately.

Resume:
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
                    f"Resume:\n{document_text}",
                    f"Resume chunk {chunk['chunk_id']}:\n{chunk['text']}",
                )
                parsed_rows.append(
                    (
                        chunk,
                        await self._generate_resume_json_with_retry(
                            system_prompt=system_prompt,
                            user_prompt=chunk_prompt,
                            max_tokens=3600,
                            db=db,
                            trace_suffix=f"batch_{index}" if len(chunks) > 1 else None,
                        ),
                    )
                )
            parsed, parser_provenance = self.document_batcher.merge(
                parsed_rows,
                list_fields={
                    "enabled_sections",
                    "target_roles",
                    "education",
                    "skills",
                    "projects",
                    "work_experience",
                    "campus_experience",
                    "certifications",
                    "awards",
                    "languages",
                    "portfolio_links",
                },
            )
            parsed["raw_text"] = raw_text
            parsed["prompt_injection"] = injection.model_dump()
            normalized = ProfileStructured.model_validate(
                self._merge_parsed_with_heuristic(heuristic, parsed)
            ).model_dump()
            normalized, rejected_fields = self._remove_unsupported_taxonomy_fields(
                grounding_source,
                normalized,
            )
            normalized, rejected_metadata = self._remove_invalid_profile_metadata(normalized, heuristic)
            rejected_fields.extend(rejected_metadata)
            quality_gate = self.grounding.evaluate_resume(grounding_source, normalized)
            quality_gate["rejected_optional_fields"] = rejected_fields
            quality_gate["rejected_optional_field_count"] = len(rejected_fields)
            normalized["quality_gate"] = quality_gate
            normalized["parser_provenance"] = parser_provenance
            if not quality_gate["passed"]:
                raise LLMResponseError(
                    "Resume parser quality gate rejected unsupported structured fields: "
                    f"{quality_gate['unsupported_fields'][:6]}"
                )
            return normalized
        except Exception:
            if not self.settings.llm_fallback_enabled:
                raise
            heuristic["prompt_injection"] = injection.model_dump()
            heuristic["quality_gate"] = self.grounding.evaluate_resume(grounding_source, heuristic)
            return heuristic

    async def _generate_resume_json_with_retry(
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
            trace_name = (
                "resume_parser.parse_structured_resume"
                if attempt == 0
                else f"resume_parser.parse_structured_resume.retry_{attempt}"
            )
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
                if attempt >= max_attempts - 1 or not self._is_transient_llm_error(format_exception(exc)):
                    raise
                continue
            return extract_json_object(text)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Resume parser LLM call did not return JSON.")

    def _is_transient_llm_error(self, message: str) -> bool:
        transient_terms = [
            "ReadTimeout",
            "ConnectTimeout",
            "RemoteProtocolError",
            "LLM returned empty content",
            "temporarily unavailable",
            "connection reset",
            "server disconnected",
        ]
        return any(term.lower() in message.lower() for term in transient_terms)

    def _create_profile(
        self,
        db: Session,
        *,
        structured: dict,
        source_type: str,
        pages: list[PDFPageText] | None = None,
    ) -> Profile:
        normalized = ProfileStructured.model_validate(structured).model_dump()
        profile = Profile(
            name=normalized.get("name"),
            email=normalized.get("email"),
            phone=normalized.get("phone"),
            headline=normalized.get("headline"),
            target_roles_json=normalized.get("target_roles", []),
            source_type=source_type,
            raw_resume_text=normalized.get("raw_text") or "",
            structured_profile_json=normalized,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)

        chunks = self.splitter.split_structured_profile(normalized)
        if pages:
            chunks.extend(self.splitter.split_pdf_pages(pages))
        else:
            chunks.extend(self.splitter.split_raw_text(str(normalized.get("raw_text") or "")))
        self.vector_index.upsert_profile_chunks(db, profile.id, chunks)
        db.refresh(profile)
        return profile

    def _heuristic_parse(self, raw_text: str) -> dict:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        email = EMAIL_RE.search(raw_text)
        phone = PHONE_RE.search(raw_text)
        skills = self._extract_skills(raw_text)
        sections = self._section_lines(lines)

        return ProfileStructured(
            name=self._guess_name(lines),
            email=email.group(0) if email else None,
            phone=phone.group(0).strip() if phone else None,
            photo_data_url=None,
            location=None,
            availability=None,
            headline=self._guess_headline(lines),
            self_summary=(
                "\n".join(sections.get("summary", [])[:4]) or None
                if self._has_explicit_summary_heading(lines)
                else None
            ),
            enabled_sections=[],
            target_roles=[],
            education=self._parse_loose_items(sections.get("education", []), "education"),
            skills=skills,
            projects=self._parse_loose_items(sections.get("projects", []), "project"),
            work_experience=self._parse_loose_items(sections.get("experience", []), "experience"),
            campus_experience=self._parse_loose_items(sections.get("campus", []), "experience"),
            certifications=sections.get("certifications", [])[:10],
            awards=sections.get("awards", [])[:8],
            languages=[x for x in ["Chinese", "English"] if x.lower() in raw_text.lower()],
            portfolio_links=[],
            prompt_injection=self.injection_guard.detect(raw_text, source="resume").model_dump(),
            raw_text=raw_text,
        ).model_dump()

    def _merge_parsed_with_heuristic(self, heuristic: dict, parsed: dict) -> dict:
        merged = {**heuristic, **parsed}
        for key in ["name", "email", "phone", "headline"]:
            if not merged.get(key) and heuristic.get(key):
                merged[key] = heuristic[key]
        for key in ["target_roles", "skills", "projects", "work_experience", "education"]:
            if not merged.get(key) and heuristic.get(key):
                merged[key] = heuristic[key]
        return merged

    def _guess_name(self, lines: list[str]) -> str | None:
        for line in lines[:8]:
            candidate = self._name_candidate_from_line(line)
            if candidate:
                return candidate
        return None

    def _name_candidate_from_line(self, line: str) -> str | None:
        text = re.sub(r"\s+", " ", line).strip()
        if not text or EMAIL_RE.search(text) or PHONE_RE.search(text):
            return None
        if any(token.lower() in text.lower() for token in ["email", "phone", "skills", "project", "education", "target roles"]):
            return None
        candidate = re.split(r"\s[-–—|｜]\s|[，,；;]", text, maxsplit=1)[0].strip()
        candidate = re.sub(r"^(姓名|Name)\s*[:：]\s*", "", candidate, flags=re.IGNORECASE).strip()
        if not candidate:
            return None
        if re.fullmatch(r"[\u4e00-\u9fff]{2,5}", candidate):
            return candidate
        if re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}", candidate):
            return candidate
        return None

    def _extract_skills(self, text: str) -> list[str]:
        lowered = text.lower()
        found = []
        for skill in KNOWN_SKILLS:
            if skill.lower() in lowered:
                found.append(skill)
        return sorted(set(found), key=lambda x: x.lower())

    def _remove_unsupported_taxonomy_fields(
        self,
        grounding_source: str,
        parsed: dict,
    ) -> tuple[dict, list[dict[str, str]]]:
        """Reject optional inferred taxonomy values before they enter the profile."""

        output = dict(parsed)
        rejected: list[dict[str, str]] = []
        target_roles = []
        for value in output.get("target_roles") or []:
            clean = str(value or "").strip()
            if clean and self.grounding.value_supported(clean, grounding_source):
                target_roles.append(clean)
            elif clean:
                rejected.append({"field": "target_roles", "value": clean})
        skills = []
        for value in output.get("skills") or []:
            clean = str(value or "").strip()
            if clean and self.grounding.has_positive_support(clean, grounding_source):
                skills.append(clean)
            elif clean:
                rejected.append({"field": "skills", "value": clean})
        output["target_roles"] = target_roles
        output["skills"] = skills
        return output, rejected

    def _remove_invalid_profile_metadata(
        self,
        parsed: dict,
        heuristic: dict,
    ) -> tuple[dict, list[dict[str, str]]]:
        output = dict(parsed)
        rejected: list[dict[str, str]] = []
        headline = str(output.get("headline") or "").strip()
        if headline and not self._is_headline_like(headline):
            rejected.append({"field": "headline", "value": headline})
            output["headline"] = heuristic.get("headline")
        return output, rejected

    def _guess_headline(self, lines: list[str]) -> str | None:
        for line in lines[:8]:
            if self._is_headline_like(line):
                return line[:255]
        return None

    def _is_headline_like(self, value: str) -> bool:
        line = " ".join(str(value or "").split())
        lowered = line.lower()
        if not line or len(line) > 100 or re.match(
            r"^(?:经历|项目|技能|教育|专业|补充说明|experience|projects?|skills?|education|major)\s*[:：]",
            lowered,
        ):
            return False
        role_cues = (
            "candidate",
            "engineer",
            "developer",
            "intern",
            "student",
            "实习生",
            "工程师",
            "开发候选人",
            "候选人",
            "求职",
            "学生",
            "本科生",
            "研究生",
        )
        return any(cue in lowered for cue in role_cues)

    def _section_lines(self, lines: list[str]) -> dict[str, list[str]]:
        section: str | None = None
        output: dict[str, list[str]] = {
            "summary": [],
            "education": [],
            "skills": [],
            "projects": [],
            "experience": [],
            "campus": [],
            "certifications": [],
            "awards": [],
        }
        for line in lines:
            detected = self._resume_section_heading(line)
            if detected:
                section = detected
                continue
            if section in output:
                output[section].append(line)
        return output

    def _resume_section_heading(self, value: str) -> str | None:
        key = re.sub(r"[\s:：|｜/_-]+", "", str(value or "").strip().lower())
        aliases = {
            "summary": {"summary", "profile", "个人总结", "自我评价", "个人优势"},
            "education": {"education", "educationbackground", "教育", "教育经历", "教育背景", "学历", "学历信息"},
            "skills": {"skills", "skill", "专业技能", "技能", "技术能力", "技能清单"},
            "projects": {"project", "projects", "projectexperience", "项目", "项目经历", "项目经验"},
            "experience": {"experience", "workexperience", "internshipexperience", "实习", "实习经历", "工作经历", "工作经验"},
            "campus": {"campus", "campusexperience", "校园经历", "社团经历", "学生会经历", "实践经历"},
            "certifications": {"certificate", "certificates", "certification", "certifications", "证书", "技能证书"},
            "awards": {"award", "awards", "honor", "honors", "获奖", "荣誉", "荣誉奖项"},
        }
        for section, names in aliases.items():
            if key in names:
                return section
        return None

    def _parse_loose_items(self, lines: list[str], kind: str) -> list[dict]:
        if not lines:
            return []
        if kind == "education":
            groups = self._split_education_items(lines)
            return [self._map_loose_education(group) for group in groups]
        if kind == "experience":
            joined = "\n".join(lines[:24])
            return [{"company": "", "role": lines[0][:120], "duration": "", "details": joined, "tech_stack": []}]
        groups = self._split_project_items(lines)
        return [self._map_loose_project(group) for group in groups]

    def _has_explicit_summary_heading(self, lines: list[str]) -> bool:
        headings = {"summary", "profile", "个人总结", "自我评价", "个人优势"}
        return any(line.lower().strip(" :：") in headings for line in lines)

    def _split_education_items(self, lines: list[str]) -> list[list[str]]:
        starts = [
            index for index, line in enumerate(lines)
            if re.search(r"(?:大学|学院|University|College)", line, flags=re.IGNORECASE)
            and len(line) <= 120
        ]
        if not starts:
            return [lines[:24]]
        groups = []
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(lines)
            groups.append(lines[start:end])
        return groups

    def _map_loose_education(self, lines: list[str]) -> dict:
        duration = next((line for line in lines if self._looks_like_duration(line)), "")
        degree = next(
            (line for line in lines[1:5] if re.search(r"本科|学士|硕士|研究生|博士|大专|Bachelor|Master|PhD", line, re.I)),
            "",
        )
        ignored = {lines[0], duration, degree}
        major = next(
            (line for line in lines[1:6] if line not in ignored and not re.search(r"GPA|排名|奖学金", line, re.I)),
            "",
        )
        details = "\n".join(line for line in lines[1:] if line not in {degree, major, duration})
        return {
            "school": lines[0][:120], "degree": degree[:80], "major": major[:120],
            "duration": duration[:80], "details": details,
        }

    def _split_project_items(self, lines: list[str]) -> list[list[str]]:
        starts = [
            index for index in range(len(lines) - 1)
            if self._looks_like_duration(lines[index + 1])
            and not self._looks_like_duration(lines[index])
            and not re.match(r"^(?:技术栈|Tech Stack)\s*[:：]", lines[index], re.I)
        ]
        if not starts:
            return [lines[:32]]
        groups = []
        for position, start in enumerate(starts):
            end = starts[position + 1] if position + 1 < len(starts) else len(lines)
            groups.append(lines[start:end])
        return groups

    def _map_loose_project(self, lines: list[str]) -> dict:
        joined = "\n".join(lines)
        description_lines = lines[1:]
        description = "\n".join(description_lines)
        return {
            "name": lines[0][:120],
            "description": description,
            "tech_stack": self._extract_skills(joined),
            "impact": "",
        }

    def _looks_like_duration(self, value: str) -> bool:
        text = str(value or "").strip()
        return bool(re.search(
            r"(?:19|20)\d{2}(?:[./年-]\d{1,2})?\s*(?:-|–|—|至|~)\s*(?:(?:19|20)\d{2}(?:[./年-]\d{1,2})?|至今|现在|Present)",
            text,
            flags=re.IGNORECASE,
        ))

    def _guided_payload_to_text(self, payload: GuidedProfileRequest) -> str:
        parts = [payload.name]
        if payload.headline:
            parts.append(payload.headline)
        if payload.email:
            parts.append(payload.email)
        if payload.phone:
            parts.append(payload.phone)
        if payload.location:
            parts.append("Location: " + payload.location)
        if payload.availability:
            parts.append("Availability: " + payload.availability)
        if payload.target_roles:
            parts.append("Target roles: " + ", ".join(payload.target_roles))
        if payload.self_summary:
            parts.append("Summary: " + payload.self_summary)
        if payload.portfolio_links:
            parts.append("Portfolio: " + "; ".join(payload.portfolio_links))
        if payload.skills:
            parts.append("Skills: " + ", ".join(payload.skills))
        for project in payload.projects:
            parts.append(f"Project: {project.name}\n{project.description}\n{project.impact}")
        for exp in payload.work_experience:
            parts.append(f"Experience: {exp.company} {exp.role}\n{exp.details}")
        for exp in payload.campus_experience:
            parts.append(f"Campus experience: {exp.company} {exp.role}\n{exp.details}")
        for edu in payload.education:
            parts.append(f"Education: {edu.school} {edu.degree} {edu.major}\n{edu.details}")
        if payload.certifications:
            parts.append("Certifications: " + "; ".join(payload.certifications))
        if payload.awards:
            parts.append("Awards: " + "; ".join(payload.awards))
        if payload.languages:
            parts.append("Languages: " + "; ".join(payload.languages))
        return "\n\n".join(part for part in parts if part)
