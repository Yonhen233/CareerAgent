import difflib
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.llm import LLMClient
from app.core.llm import LLMConfigurationError, LLMResponseError
from app.models.entities import Job, Profile, ResumeVersion
from app.services.context_compressor import ContextCompressor
from app.services.guardrails import ResumeGuardrailService
from app.services.matcher import MatcherService


NEGATIVE_EVIDENCE_CUES = [
    "no ",
    "did not",
    "do not",
    "does not",
    "not implement",
    "not build",
    "without ",
    "lacks ",
    "lack ",
    "coursework only",
    "read articles",
    "read papers",
    "planned",
]


class ResumeTailorService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMClient()
        self.matcher = MatcherService()
        self.guardrails = ResumeGuardrailService()
        self.context_compressor = ContextCompressor()

    async def tailor_resume(self, db: Session, profile: Profile, job: Job) -> ResumeVersion:
        evidence = self.matcher.retrieve_evidence(db, profile.id, job, top_k=10)
        compressed_context = self.context_compressor.compress_tailor_context(
            profile=profile,
            job=job,
            evidence=evidence,
        )
        if self.llm.available:
            draft = await self._llm_tailor(db, profile, job, evidence, compressed_context)
        else:
            if not self.settings.llm_fallback_enabled:
                raise LLMConfigurationError(
                    "LLM is required for resume tailoring. Set LLM_FALLBACK_ENABLED=true for tests."
                )
            draft = self._heuristic_tailor(profile, job, evidence)

        markdown = str(draft.get("tailored_resume_markdown") or "").strip()
        if not markdown:
            if not self.settings.llm_fallback_enabled:
                raise LLMResponseError("LLM resume tailoring returned an empty tailored_resume_markdown.")
            draft = self._heuristic_tailor(profile, job, evidence)
            markdown = draft["tailored_resume_markdown"]

        verification = self.guardrails.verify(
            profile=profile,
            job=job,
            resume_markdown=markdown,
            evidence=evidence,
        )
        version = ResumeVersion(
            profile_id=profile.id,
            job_id=job.id,
            title=f"{profile.name or 'Candidate'} - {job.title}",
            tailored_resume_markdown=markdown,
            change_summary_json=draft.get("change_summary", []),
            keyword_alignment_json={
                **(draft.get("keyword_alignment", {}) if isinstance(draft.get("keyword_alignment"), dict) else {}),
                "context_compression": compressed_context.get("context_compression", {}),
            },
            source_evidence_json=evidence,
            verification_json=verification,
            diff_text=self._build_diff(profile.raw_resume_text, markdown),
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return version

    async def _llm_tailor(
        self,
        db: Session,
        profile: Profile,
        job: Job,
        evidence: list[dict[str, Any]],
        compressed_context: dict[str, Any],
    ) -> dict[str, Any]:
        system_prompt = (
            "You are a senior resume writing agent. Return strict JSON only. "
            "You must never fabricate facts, metrics, companies, degrees, or dates."
        )
        user_prompt = f"""
Goal: tailor the candidate resume for this job while staying grounded in the compressed source context.

Output JSON:
{{
  "tailored_resume_markdown": string,
  "change_summary": [{{"section": string, "change": string, "reason": string}}],
  "keyword_alignment": {{"covered": [string], "missing": [string], "notes": [string]}}
}}

Hard rules:
- Use only facts present in source profile or evidence chunks.
- You may reorder, summarize, and emphasize; do not invent metrics or claims.
- Do not add missing JD requirements to tailored_resume_markdown, even as "eager to learn", "seeking exposure", or future intent.
- Put missing or weakly supported JD requirements only in keyword_alignment.missing/notes, not in the resume body.
- If evidence says the candidate did not build or lacks a skill, do not present that skill as covered in the resume.
- Keep the resume concise and ATS-friendly.
- Prefer Agent/RAG/FastAPI/SQLite/tool-calling evidence when relevant.
- The context was compressed. If a fact is absent from compressed_context, treat it as unavailable.

Compressed context:
{json.dumps(compressed_context, ensure_ascii=False)}
"""
        try:
            return await self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                db=db,
                trace_name="resume_tailor.tailor_resume",
            )
        except Exception:
            if not self.settings.llm_fallback_enabled:
                raise
            return self._heuristic_tailor(profile, job, evidence)

    def _heuristic_tailor(self, profile: Profile, job: Job, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        data = profile.structured_profile_json or {}
        job_data = job.structured_jd_json or {}
        required = [str(x) for x in job_data.get("required_skills", [])]
        profile_skills = [str(x) for x in data.get("skills", [])]
        aligned_skills = [skill for skill in profile_skills if skill.lower() in " ".join(required).lower()]
        if not aligned_skills:
            aligned_skills = profile_skills[:12]

        lines = [
            f"# {profile.name or 'Candidate'}",
            "",
            profile.headline or "Agent / LLM Application Developer",
        ]
        contacts = [x for x in [profile.email, profile.phone] if x]
        if contacts:
            lines.extend(["", " | ".join(contacts)])

        lines.extend(["", "## Target Role", f"{job.title} at {job.company or 'target company'}"])
        if aligned_skills:
            lines.extend(["", "## Skills", ", ".join(aligned_skills)])

        evidence_projects = [item for item in evidence if item.get("chunk_type") in {"project", "experience"}]
        lines.extend(["", "## Selected Evidence"])
        for item in evidence_projects[:5]:
            text = self._safe_evidence_text(str(item.get("text") or ""))
            if not text:
                continue
            lines.append(f"- {text[:280]}")
        if not evidence_projects:
            for project in data.get("projects", [])[:4]:
                name = project.get("name", "Project") if isinstance(project, dict) else "Project"
                desc = project.get("description", "") if isinstance(project, dict) else str(project)
                impact = project.get("impact", "") if isinstance(project, dict) else ""
                lines.append(f"- {name}: {desc} {impact}".strip())

        if data.get("work_experience"):
            lines.extend(["", "## Experience"])
            for exp in data.get("work_experience", [])[:4]:
                if not isinstance(exp, dict):
                    continue
                heading = " - ".join(x for x in [exp.get("company"), exp.get("role"), exp.get("duration")] if x)
                lines.append(f"- {heading}: {exp.get('details', '')}".strip())

        if data.get("education"):
            lines.extend(["", "## Education"])
            for edu in data.get("education", [])[:3]:
                if not isinstance(edu, dict):
                    continue
                lines.append(
                    "- "
                    + " ".join(
                        str(x)
                        for x in [edu.get("school"), edu.get("degree"), edu.get("major"), edu.get("duration")]
                        if x
                    )
                )

        covered = [skill for skill in required if skill.lower() in "\n".join(lines).lower()]
        missing = [skill for skill in required if skill not in covered]
        return {
            "tailored_resume_markdown": "\n".join(lines).strip(),
            "change_summary": [
                {
                    "section": "Selected Evidence",
                    "change": "Prioritized retrieved resume chunks that match the JD.",
                    "reason": "Grounded RAG evidence improves relevance while reducing hallucination risk.",
                }
            ],
            "keyword_alignment": {"covered": covered, "missing": missing, "notes": ["Generated by explicit test fallback."]},
        }

    def _safe_evidence_text(self, text: str) -> str:
        safe_parts: list[str] = []
        normalized = text.replace("\n", " ")
        for sentence in re.split(r"[.!?。！？]", normalized):
            stripped = sentence.strip(" |")
            if not stripped:
                continue
            lowered = stripped.lower()
            if " but did not" in lowered:
                stripped = stripped[: lowered.index(" but did not")].strip(" |")
                lowered = stripped.lower()
            if any(cue in lowered for cue in NEGATIVE_EVIDENCE_CUES):
                continue
            safe_parts.append(stripped)
        return ". ".join(safe_parts)

    def _build_diff(self, original: str, tailored: str) -> str:
        return "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                tailored.splitlines(),
                fromfile="original_resume",
                tofile="tailored_resume",
                lineterm="",
            )
        )
