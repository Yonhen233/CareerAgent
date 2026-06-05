import re

from app.core.llm import LLMClient
from app.models.schemas import JDStructured
from app.services.resume_parser import KNOWN_SKILLS


class JDParserService:
    def __init__(self) -> None:
        self.llm = LLMClient()

    async def parse_jd(
        self,
        raw_text: str,
        *,
        title: str | None = None,
        company: str | None = None,
        location: str | None = None,
    ) -> dict:
        heuristic = self.heuristic_parse(raw_text, title=title, company=company, location=location)
        if not self.llm.available:
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
            parsed = await self.llm.generate_json(system_prompt=system_prompt, user_prompt=user_prompt)
            return JDStructured.model_validate({**heuristic, **parsed}).model_dump()
        except Exception:
            return heuristic

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
        skills = self._extract_skills(raw_text)
        responsibilities, qualifications = self._split_responsibilities(lines)
        keywords = sorted(set(skills + self._keyword_phrases(raw_text)))[:30]
        job_type = self._guess_job_type(raw_text, guessed_title)
        return JDStructured(
            title=guessed_title,
            company=company,
            location=location,
            job_type=job_type,
            required_skills=skills[:16],
            preferred_skills=[],
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

    def _extract_skills(self, text: str) -> list[str]:
        lowered = text.lower()
        found = []
        for skill in KNOWN_SKILLS:
            if skill.lower() in lowered:
                found.append(skill)
        extra_patterns = ["machine learning", "retrieval", "tool calling", "workflow", "evaluation", "guardrail"]
        for phrase in extra_patterns:
            if phrase in lowered:
                found.append(phrase.title())
        return sorted(set(found), key=lambda x: x.lower())

    def _split_responsibilities(self, lines: list[str]) -> tuple[list[str], list[str]]:
        responsibilities: list[str] = []
        qualifications: list[str] = []
        mode = "responsibility"
        for line in lines:
            lowered = line.lower()
            if any(token in lowered for token in ["qualification", "requirement", "任职", "要求"]):
                mode = "qualification"
                continue
            if any(token in lowered for token in ["responsibil", "what you", "岗位职责", "工作内容"]):
                mode = "responsibility"
                continue
            if len(line) < 8:
                continue
            if mode == "qualification":
                qualifications.append(line)
            else:
                responsibilities.append(line)
        return responsibilities, qualifications

    def _keyword_phrases(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-\+#\.]{2,}", text)
        stop = {"and", "the", "with", "for", "you", "our", "are", "will", "this", "that"}
        return [token for token in tokens if token.lower() not in stop and len(token) <= 24]

    def _guess_job_type(self, text: str, title: str | None) -> str | None:
        haystack = f"{title or ''}\n{text}".lower()
        if any(token in haystack for token in ["intern", "实习", "internship"]):
            return "internship"
        if "remote" in haystack:
            return "remote"
        if "part-time" in haystack or "兼职" in haystack:
            return "part-time"
        if "full-time" in haystack or "全职" in haystack:
            return "full-time"
        return None
