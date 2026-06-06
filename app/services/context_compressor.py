import json
import re
from typing import Any, Callable

from app.core.config import get_settings
from app.models.entities import Job, Profile


class ContextCompressor:
    """Build budgeted prompt packets with progressive disclosure metadata."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def compress_tailor_context(
        self,
        *,
        profile: Profile,
        job: Job,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        profile_data = profile.structured_profile_json or {}
        job_data = job.structured_jd_json or {}
        raw_context = {
            "profile": profile_data,
            "raw_resume_text": profile.raw_resume_text,
            "job": job_data,
            "raw_jd_text": job.raw_jd_text,
            "evidence": evidence,
        }
        profile_layer = self._profile_layer(profile_data, profile.raw_resume_text)
        job_layer = self._job_layer(job, job_data)
        evidence_layer = self._evidence_layer(evidence)
        packet = {
            "task": "resume_tailoring",
            "progressive_disclosure": self._progressive_disclosure_contract(),
            "profile_facts": profile_layer["payload"],
            "job_requirements": job_layer["payload"],
            "ranked_evidence": evidence_layer["payload"],
            "instructions": {
                "grounding": "Use only facts present in profile_facts or ranked_evidence.",
                "negative_evidence": (
                    "Treat planned learning, coursework-only notes, abandoned prototypes and missing delivery "
                    "as gaps, not achievements."
                ),
                "rewrite_scope": "Reorder, summarize and emphasize existing facts; do not invent metrics.",
            },
        }
        return self._finalize(
            raw_context,
            packet,
            purpose="resume_tailoring",
            layers=[profile_layer, job_layer, evidence_layer],
        )

    def compress_fit_context(self, *, profile_json: dict[str, Any], job: Job) -> dict[str, Any]:
        raw_context = {
            "profile": profile_json,
            "raw_resume_text": profile_json.get("raw_text", ""),
            "job": job.structured_jd_json or {},
            "raw_jd_text": job.raw_jd_text,
        }
        profile_layer = self._profile_layer(profile_json, str(profile_json.get("raw_text") or ""))
        job_layer = self._job_layer(job, job.structured_jd_json or {})
        packet = {
            "task": "fit_judge",
            "progressive_disclosure": self._progressive_disclosure_contract(),
            "profile_facts": profile_layer["payload"],
            "job_requirements": job_layer["payload"],
            "evaluation_rules": {
                "strong_fit": "Direct shipped evidence for most core requirements.",
                "partial_fit": "Meaningful overlap, but important missing or adjacent evidence remains.",
                "weak_fit": "Mostly coursework, planned learning, unrelated prototype or role mismatch.",
            },
        }
        return self._finalize(raw_context, packet, purpose="fit_judge", layers=[profile_layer, job_layer])

    def _profile_layer(self, profile_data: dict[str, Any], raw_resume_text: str) -> dict[str, Any]:
        payload = self._compress_profile(profile_data, raw_resume_text, level=0)
        return self._budget_layer(
            name="profile_summary",
            raw_payload={"profile": profile_data, "raw_resume_text": raw_resume_text},
            payload=payload,
            budget=max(int(self.settings.llm_context_max_chars * 0.32), 1800),
            strategy="structured_profile_fields_then_signal_excerpt",
            shrinkers=[
                lambda item: self._compress_profile(profile_data, raw_resume_text, level=1),
            ],
        )

    def _job_layer(self, job: Job, job_data: dict[str, Any]) -> dict[str, Any]:
        payload = self._compress_job(job, job_data, level=0)
        return self._budget_layer(
            name="job_summary",
            raw_payload={"job": job_data, "raw_jd_text": job.raw_jd_text},
            payload=payload,
            budget=max(int(self.settings.llm_context_max_chars * 0.26), 1600),
            strategy="structured_jd_requirements_then_signal_excerpt",
            shrinkers=[
                lambda item: self._compress_job(job, job_data, level=1),
            ],
        )

    def _evidence_layer(self, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        payload = self._compress_evidence(evidence, max_items=20, snippet_chars=520)
        return self._budget_layer(
            name="evidence_snippets",
            raw_payload={"evidence": evidence},
            payload=payload,
            budget=self.settings.llm_evidence_max_chars,
            strategy="top20_retrieval_rerank_metadata_then_budgeted_snippets",
            shrinkers=[
                lambda item: self._compress_evidence(evidence, max_items=6, snippet_chars=180),
            ],
        )

    def _budget_layer(
        self,
        *,
        name: str,
        raw_payload: Any,
        payload: Any,
        budget: int,
        strategy: str,
        shrinkers: list[Callable[[Any], Any]],
    ) -> dict[str, Any]:
        raw_chars = self._chars(raw_payload)
        current = payload
        events: list[dict[str, Any]] = []
        for idx, shrinker in enumerate(shrinkers, start=1):
            current_chars = self._chars(current)
            if current_chars <= budget:
                break
            next_payload = shrinker(current)
            next_chars = self._chars(next_payload)
            events.append(
                {
                    "stage": f"{name}_shrink_{idx}",
                    "before_chars": current_chars,
                    "after_chars": next_chars,
                    "budget_chars": budget,
                }
            )
            current = next_payload
        output_chars = self._chars(current)
        return {
            "name": name,
            "strategy": strategy,
            "visible_to_llm": True,
            "input_chars": raw_chars,
            "output_chars": output_chars,
            "budget_chars": budget,
            "dropped_chars": max(raw_chars - output_chars, 0),
            "within_budget": output_chars <= budget,
            "events": events,
            "payload": current,
        }

    def _finalize(
        self,
        raw_context: dict[str, Any],
        packet: dict[str, Any],
        *,
        purpose: str,
        layers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raw_chars = self._chars(raw_context)
        initial_packet_chars = self._chars(packet)
        if not self.settings.llm_context_compression_enabled:
            payload = dict(packet)
            payload["raw_context"] = raw_context
            events: list[dict[str, Any]] = []
        else:
            payload = dict(packet)
            events = []
            current_chars = self._chars(payload)
            if current_chars > self.settings.llm_context_max_chars:
                payload = self._shrink_prompt_packet(payload)
                events.append(
                    self._event(
                        "prompt_packet_budget_trim",
                        before_chars=current_chars,
                        after_chars=self._chars(payload),
                        budget_chars=self.settings.llm_context_max_chars,
                    )
                )

        final_chars = self._chars(payload)
        layer_metadata = [{key: value for key, value in layer.items() if key != "payload"} for layer in layers]
        payload["context_compression"] = {
            "enabled": self.settings.llm_context_compression_enabled,
            "purpose": purpose,
            "strategy": "progressive_disclosure_budgeted_packet",
            "raw_chars": raw_chars,
            "initial_packet_chars": initial_packet_chars,
            "compressed_chars": final_chars,
            "max_chars": self.settings.llm_context_max_chars,
            "reduction_ratio": round(max(0, 1 - final_chars / max(raw_chars, 1)), 4),
            "expansion_ratio": round(max(0, final_chars / max(raw_chars, 1) - 1), 4),
            "dropped_chars": max(raw_chars - final_chars, 0),
            "evidence_max_chars": self.settings.llm_evidence_max_chars,
            "retained_evidence_count": len(payload.get("ranked_evidence", [])),
            "levels": layer_metadata
            + [
                {
                    "name": "prompt_packet",
                    "strategy": "single_budget_guard",
                    "visible_to_llm": True,
                    "input_chars": initial_packet_chars,
                    "output_chars": final_chars,
                    "budget_chars": self.settings.llm_context_max_chars,
                    "dropped_chars": max(initial_packet_chars - final_chars, 0),
                    "within_budget": final_chars <= self.settings.llm_context_max_chars,
                    "events": events,
                }
            ],
        }
        return payload

    def _compress_profile(self, profile_data: dict[str, Any], raw_resume_text: str, *, level: int) -> dict[str, Any]:
        if level == 0:
            project_count, exp_count, skill_count, desc_chars, impact_chars, excerpt_chars = 8, 6, 32, 520, 260, 1200
        else:
            project_count, exp_count, skill_count, desc_chars, impact_chars, excerpt_chars = 4, 3, 24, 320, 160, 650

        projects = []
        for project in profile_data.get("projects", [])[:project_count]:
            if not isinstance(project, dict):
                continue
            projects.append(
                {
                    "name": project.get("name", ""),
                    "description": self._trim(project.get("description", ""), desc_chars),
                    "tech_stack": project.get("tech_stack", [])[: min(skill_count, 12)],
                    "impact": self._trim(project.get("impact", ""), impact_chars),
                }
            )
        experiences = []
        for exp in profile_data.get("work_experience", [])[:exp_count]:
            if not isinstance(exp, dict):
                continue
            experiences.append(
                {
                    "company": exp.get("company", ""),
                    "role": exp.get("role", ""),
                    "duration": exp.get("duration", ""),
                    "details": self._trim(exp.get("details", ""), desc_chars),
                    "tech_stack": exp.get("tech_stack", [])[: min(skill_count, 12)],
                }
            )
        return {
            "name": profile_data.get("name"),
            "headline": profile_data.get("headline"),
            "target_roles": profile_data.get("target_roles", [])[:8],
            "skills": profile_data.get("skills", [])[:skill_count],
            "projects": projects,
            "work_experience": experiences,
            "education": profile_data.get("education", [])[:4],
            "awards": profile_data.get("awards", [])[:8 if level == 0 else 4],
            "raw_text_excerpt": self._extract_signal_sentences(raw_resume_text, limit_chars=excerpt_chars),
        }

    def _compress_job(self, job: Job, job_data: dict[str, Any], *, level: int) -> dict[str, Any]:
        if level == 0:
            required_count, preferred_count, list_count, keyword_count, excerpt_chars = 24, 18, 8, 28, 1600
        else:
            required_count, preferred_count, list_count, keyword_count, excerpt_chars = 16, 10, 5, 20, 700
        return {
            "title": job.title or job_data.get("title"),
            "company": job.company or job_data.get("company"),
            "location": job.location or job_data.get("location"),
            "job_type": job.job_type or job_data.get("job_type"),
            "required_skills": job_data.get("required_skills", [])[:required_count],
            "preferred_skills": job_data.get("preferred_skills", [])[:preferred_count],
            "responsibilities": [self._trim(item, 260) for item in job_data.get("responsibilities", [])[:list_count]],
            "qualifications": [self._trim(item, 260) for item in job_data.get("qualifications", [])[:list_count]],
            "keywords": job_data.get("keywords", [])[:keyword_count],
            "raw_jd_excerpt": self._extract_signal_sentences(job.raw_jd_text, limit_chars=excerpt_chars),
        }

    def _compress_evidence(
        self,
        evidence: list[dict[str, Any]],
        *,
        max_items: int,
        snippet_chars: int,
    ) -> list[dict[str, Any]]:
        retained = []
        used_chars = 0
        for item in evidence[:max_items]:
            text = str(item.get("text") or "")
            if not text.strip():
                continue
            remaining = max(self.settings.llm_evidence_max_chars - used_chars, 0)
            if remaining <= 0:
                break
            snippet = self._trim(text, min(remaining, snippet_chars))
            used_chars += len(snippet)
            retained.append(
                {
                    "chunk_uid": item.get("chunk_uid"),
                    "chunk_type": item.get("chunk_type"),
                    "score": item.get("score"),
                    "source": item.get("source"),
                    "text": snippet,
                    "retrieval": self._compact_metadata((item.get("metadata") or {}).get("retrieval")),
                    "rerank": self._compact_metadata((item.get("metadata") or {}).get("rerank")),
                }
            )
        return retained

    def _shrink_prompt_packet(self, payload: dict[str, Any]) -> dict[str, Any]:
        shrunk = dict(payload)
        shrunk["ranked_evidence"] = shrunk.get("ranked_evidence", [])[:5]
        self._trim_evidence_in_place(shrunk["ranked_evidence"], 180)
        profile = dict(shrunk.get("profile_facts") or {})
        profile["raw_text_excerpt"] = self._trim(profile.get("raw_text_excerpt", ""), 350)
        profile["projects"] = profile.get("projects", [])[:3]
        profile["work_experience"] = profile.get("work_experience", [])[:2]
        profile["skills"] = profile.get("skills", [])[:20]
        shrunk["profile_facts"] = profile
        job = dict(shrunk.get("job_requirements") or {})
        job["raw_jd_excerpt"] = self._trim(job.get("raw_jd_excerpt", ""), 420)
        job["responsibilities"] = job.get("responsibilities", [])[:4]
        job["qualifications"] = job.get("qualifications", [])[:4]
        job["required_skills"] = job.get("required_skills", [])[:14]
        shrunk["job_requirements"] = job
        return shrunk

    def _trim_evidence_in_place(self, evidence: list[dict[str, Any]], max_chars: int) -> None:
        for item in evidence:
            item["text"] = self._trim(item.get("text", ""), max_chars)

    def _compact_metadata(self, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        keep = [
            "rank",
            "score",
            "first_stage_score",
            "final_score",
            "rerank_score_normalized",
            "rerank_weight",
            "anchor_top_n",
            "reranker_provider",
            "reranker_model",
        ]
        compact = {key: value.get(key) for key in keep if key in value}
        return compact or None

    def _progressive_disclosure_contract(self) -> dict[str, Any]:
        return {
            "default_visible_layers": ["profile_facts", "job_requirements", "ranked_evidence"],
            "deferred_layers": ["full_raw_resume", "full_raw_jd", "non_top_evidence_chunks"],
            "expand_rule": (
                "Only request or expose deferred context when a repair loop needs a specific missing citation; "
                "otherwise answer with the visible packet."
            ),
            "failure_rule": "If visible evidence is insufficient, report the gap instead of inventing facts.",
        }

    def _extract_signal_sentences(self, text: str, *, limit_chars: int) -> str:
        clean = re.sub(r"\s+", " ", text or "").strip()
        if len(clean) <= limit_chars:
            return clean
        signal_words = [
            "built",
            "implemented",
            "created",
            "designed",
            "evaluated",
            "deployed",
            "agent",
            "rag",
            "fastapi",
            "sqlite",
            "evaluation",
            "guardrail",
            "intern",
            "project",
            "impact",
            "metric",
            "planned",
            "coursework",
            "abandoned",
            "构建",
            "实现",
            "部署",
            "评测",
            "检索",
            "课程",
            "计划",
        ]
        sentences = re.split(r"(?<=[.!?。！？])\s+", clean)
        selected = []
        used = 0
        for sentence in sentences:
            lowered = sentence.lower()
            if not any(word in lowered for word in signal_words):
                continue
            if used + len(sentence) > limit_chars:
                break
            selected.append(sentence)
            used += len(sentence)
        if selected:
            return " ".join(selected)
        return self._trim(clean, limit_chars)

    def _trim(self, value: Any, max_chars: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(text) <= max_chars:
            return text
        return text[: max(max_chars - 3, 0)].rstrip() + "..."

    def _event(self, stage: str, *, before_chars: int, after_chars: int, budget_chars: int) -> dict[str, Any]:
        return {
            "stage": stage,
            "before_chars": before_chars,
            "after_chars": after_chars,
            "budget_chars": budget_chars,
        }

    def _chars(self, value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, default=str))
