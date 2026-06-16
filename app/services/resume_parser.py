import re
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMClient
from app.core.llm import LLMConfigurationError
from app.core.llm import extract_json_object
from app.core.llm import format_exception
from app.models.entities import Profile
from app.models.schemas import GuidedProfileRequest, ProfileStructured
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
    reader = PdfReader(str(path))
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        pages.append(PDFPageText(page_no=index, text=(page.extract_text() or "").strip()))
    return pages


class ResumeParserService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMClient()
        self.splitter = ResumeTextSplitter(self.settings.chunk_size, self.settings.chunk_overlap)
        self.vector_index = SQLiteVectorIndex()
        self.settings.upload_path.mkdir(parents=True, exist_ok=True)

    async def create_profile_from_pdf(self, db: Session, *, filename: str, file_bytes: bytes) -> Profile:
        if not file_bytes:
            raise ValueError("Uploaded file is empty.")
        path = self.settings.upload_path / f"{uuid4().hex}_{safe_filename(filename)}"
        path.write_bytes(file_bytes)
        pages = extract_pdf_pages(path)
        raw_text = "\n".join(page.text for page in pages).strip()
        if not raw_text:
            raise ValueError("No extractable text was found in the PDF.")
        structured = await self.parse_structured_resume(raw_text, db=db)
        return self._create_profile(db, structured=structured, source_type="pdf", pages=pages)

    def create_profile_from_guided_answers(self, db: Session, payload: GuidedProfileRequest) -> Profile:
        structured = ProfileStructured(
            name=payload.name,
            email=payload.email,
            phone=payload.phone,
            headline=payload.headline,
            target_roles=payload.target_roles,
            education=payload.education,
            skills=payload.skills,
            projects=payload.projects,
            work_experience=payload.work_experience,
            awards=payload.awards,
            languages=payload.languages,
            raw_text=self._guided_payload_to_text(payload),
        ).model_dump()
        return self._create_profile(db, structured=structured, source_type="guided")

    async def parse_structured_resume(self, raw_text: str, db=None) -> dict:
        heuristic = self._heuristic_parse(raw_text)
        if not self.llm.available:
            if not self.settings.llm_fallback_enabled:
                raise LLMConfigurationError(
                    "LLM is required for resume parsing. Set LLM_FALLBACK_ENABLED=true for tests."
                )
            return heuristic

        system_prompt = (
            "You are a careful resume parser. Return strict JSON only. "
            "Never infer unsupported facts."
        )
        user_prompt = f"""
Parse the resume into this JSON schema:
{{
  "name": string|null,
  "email": string|null,
  "phone": string|null,
  "headline": string|null,
  "target_roles": [string],
  "education": [{{"school": string, "degree": string, "major": string, "duration": string, "details": string}}],
  "skills": [string],
  "projects": [{{"name": string, "description": string, "tech_stack": [string], "impact": string}}],
  "work_experience": [{{"company": string, "role": string, "duration": string, "details": string, "tech_stack": [string]}}],
  "awards": [string],
  "languages": [string],
  "raw_text": string
}}

Rules:
- Use null or [] when unknown.
- Keep all facts grounded in the original text.
- raw_text must be exactly the original resume text.

Resume:
{raw_text}
"""
        try:
            parsed = await self._generate_resume_json_with_retry(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=1400,
                db=db,
            )
            parsed["raw_text"] = raw_text
            return ProfileStructured.model_validate({**heuristic, **parsed}).model_dump()
        except Exception:
            if not self.settings.llm_fallback_enabled:
                raise
            return heuristic

    async def _generate_resume_json_with_retry(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        db=None,
    ) -> dict:
        last_exc: Exception | None = None
        max_attempts = 3
        for attempt in range(max_attempts):
            trace_name = (
                "resume_parser.parse_structured_resume"
                if attempt == 0
                else f"resume_parser.parse_structured_resume.retry_{attempt}"
            )
            try:
                text = await self.llm.generate_text(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.1,
                    max_tokens=max_tokens,
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
        first_line = lines[0] if lines else None
        email = EMAIL_RE.search(raw_text)
        phone = PHONE_RE.search(raw_text)
        skills = self._extract_skills(raw_text)
        sections = self._section_lines(lines)

        return ProfileStructured(
            name=first_line if first_line and len(first_line) <= 40 else None,
            email=email.group(0) if email else None,
            phone=phone.group(0).strip() if phone else None,
            headline=self._guess_headline(lines),
            target_roles=["Agent 开发实习生"] if "agent" in raw_text.lower() else [],
            education=self._parse_loose_items(sections.get("education", []), "education"),
            skills=skills,
            projects=self._parse_loose_items(sections.get("projects", []), "project"),
            work_experience=self._parse_loose_items(sections.get("experience", []), "experience"),
            awards=sections.get("awards", [])[:8],
            languages=[x for x in ["Chinese", "English"] if x.lower() in raw_text.lower()],
            raw_text=raw_text,
        ).model_dump()

    def _extract_skills(self, text: str) -> list[str]:
        lowered = text.lower()
        found = []
        for skill in KNOWN_SKILLS:
            if skill.lower() in lowered:
                found.append(skill)
        return sorted(set(found), key=lambda x: x.lower())

    def _guess_headline(self, lines: list[str]) -> str | None:
        for line in lines[:8]:
            lowered = line.lower()
            if any(token in lowered for token in ["agent", "llm", "rag", "backend", "ai engineer"]):
                return line[:255]
        return None

    def _section_lines(self, lines: list[str]) -> dict[str, list[str]]:
        section = "summary"
        output: dict[str, list[str]] = {
            "education": [],
            "projects": [],
            "experience": [],
            "awards": [],
        }
        for line in lines:
            key = line.lower().strip(":：")
            if any(token in key for token in ["education", "教育", "学历"]):
                section = "education"
                continue
            if any(token in key for token in ["project", "项目"]):
                section = "projects"
                continue
            if any(token in key for token in ["experience", "work", "实习", "工作经历"]):
                section = "experience"
                continue
            if any(token in key for token in ["award", "honor", "获奖"]):
                section = "awards"
                continue
            if section in output:
                output[section].append(line)
        return output

    def _parse_loose_items(self, lines: list[str], kind: str) -> list[dict]:
        if not lines:
            return []
        joined = "\n".join(lines[:16])
        if kind == "education":
            return [{"school": lines[0][:120], "degree": "", "major": "", "duration": "", "details": joined}]
        if kind == "experience":
            return [{"company": "", "role": lines[0][:120], "duration": "", "details": joined, "tech_stack": []}]
        return [{"name": lines[0][:120], "description": joined, "tech_stack": self._extract_skills(joined), "impact": ""}]

    def _guided_payload_to_text(self, payload: GuidedProfileRequest) -> str:
        parts = [payload.name]
        if payload.headline:
            parts.append(payload.headline)
        if payload.email:
            parts.append(payload.email)
        if payload.phone:
            parts.append(payload.phone)
        if payload.target_roles:
            parts.append("Target roles: " + ", ".join(payload.target_roles))
        if payload.skills:
            parts.append("Skills: " + ", ".join(payload.skills))
        for project in payload.projects:
            parts.append(f"Project: {project.name}\n{project.description}\n{project.impact}")
        for exp in payload.work_experience:
            parts.append(f"Experience: {exp.company} {exp.role}\n{exp.details}")
        for edu in payload.education:
            parts.append(f"Education: {edu.school} {edu.degree} {edu.major}\n{edu.details}")
        if payload.awards:
            parts.append("Awards: " + "; ".join(payload.awards))
        return "\n\n".join(part for part in parts if part)
