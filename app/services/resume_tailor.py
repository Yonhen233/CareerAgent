import difflib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import LLMClient
from app.models.entities import Job, Profile, ResumeVersion
from app.services.guardrails import ResumeGuardrailService
from app.services.matcher import MatcherService


class ResumeTailorService:
    def __init__(self) -> None:
        self.llm = LLMClient()
        self.matcher = MatcherService()
        self.guardrails = ResumeGuardrailService()

    async def tailor_resume(self, db: Session, profile: Profile, job: Job) -> ResumeVersion:
        evidence = self.matcher.retrieve_evidence(db, profile.id, job, top_k=10)
        if self.llm.available:
            draft = await self._llm_tailor(profile, job, evidence)
        else:
            draft = self._heuristic_tailor(profile, job, evidence)

        markdown = str(draft.get("tailored_resume_markdown") or "").strip()
        if not markdown:
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
            keyword_alignment_json=draft.get("keyword_alignment", {}),
            source_evidence_json=evidence,
            verification_json=verification,
            diff_text=self._build_diff(profile.raw_resume_text, markdown),
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return version

    async def _llm_tailor(self, profile: Profile, job: Job, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        system_prompt = (
            "You are a senior resume writing agent. Return strict JSON only. "
            "You must never fabricate facts, metrics, companies, degrees, or dates."
        )
        user_prompt = f"""
Goal: tailor the candidate resume for this job while staying grounded in source evidence.

Output JSON:
{{
  "tailored_resume_markdown": string,
  "change_summary": [{{"section": string, "change": string, "reason": string}}],
  "keyword_alignment": {{"covered": [string], "missing": [string], "notes": [string]}}
}}

Hard rules:
- Use only facts present in source profile or evidence chunks.
- You may reorder, summarize, and emphasize; do not invent metrics or claims.
- Keep the resume concise and ATS-friendly.
- Prefer Agent/RAG/FastAPI/SQLite/tool-calling evidence when relevant.

Source profile JSON:
{json.dumps(profile.structured_profile_json, ensure_ascii=False)}

Source raw resume:
{profile.raw_resume_text}

Job JSON:
{json.dumps(job.structured_jd_json, ensure_ascii=False)}

Job description:
{job.raw_jd_text}

Retrieved evidence:
{json.dumps(evidence, ensure_ascii=False)}
"""
        try:
            return await self.llm.generate_json(system_prompt=system_prompt, user_prompt=user_prompt)
        except Exception:
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
            text = str(item.get("text") or "").replace("\n", " ")
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
            "keyword_alignment": {"covered": covered, "missing": missing, "notes": ["Generated by deterministic fallback."]},
        }

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
