from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.llm import LLMClient, extract_json_object
from app.models.entities import Profile


@dataclass(frozen=True)
class JobSearchIntent:
    retrieval_query: str
    query_variants: list[str]
    locations: list[str] = field(default_factory=list)
    excluded_terms: list[str] = field(default_factory=list)
    planner_mode: str = "deterministic"
    profile_inference_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "retrieval_query": self.retrieval_query,
            "query_variants": self.query_variants,
            "locations": self.locations,
            "excluded_terms": self.excluded_terms,
            "planner_mode": self.planner_mode,
            "profile_inference_used": self.profile_inference_used,
        }


class JobSearchIntentService:
    """Turn free-form preferences and profile evidence into bounded retrieval intent."""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    async def plan(
        self,
        db: Session,
        *,
        preference: str,
        profile: Profile | None,
        explicit_location: str | None,
    ) -> JobSearchIntent:
        fallback = self._evidence_preserving_plan(
            preference=preference,
            profile=profile,
            explicit_location=explicit_location,
        )
        if not self.llm.available:
            return fallback
        try:
            planned = await self._llm_plan(db, preference=preference, profile=profile, fallback=fallback)
            return planned
        except Exception:  # noqa: BLE001 - search must survive optional semantic planning failure
            return fallback

    def _evidence_preserving_plan(
        self,
        *,
        preference: str,
        profile: Profile | None,
        explicit_location: str | None,
    ) -> JobSearchIntent:
        locations = self._split_explicit_values(explicit_location)
        profile_queries = self._profile_queries(profile)
        preference_queries = self._text_segments(preference)
        if preference_queries:
            # Keep the user's words intact when semantic interpretation is unavailable.
            variants = self._unique([preference, *preference_queries])
            primary = preference
        elif profile_queries:
            variants = profile_queries
            primary = profile_queries[0]
        else:
            primary = "Agent 开发 实习 校招"
            variants = [primary]
        return JobSearchIntent(
            retrieval_query=primary,
            query_variants=variants[:3],
            locations=locations,
            excluded_terms=[],
            planner_mode="profile_evidence_retrieval" if profile and not preference else "raw_semantic_retrieval",
            profile_inference_used=not bool(preference.strip()) and bool(profile),
        )

    async def _llm_plan(
        self,
        db: Session,
        *,
        preference: str,
        profile: Profile | None,
        fallback: JobSearchIntent,
    ) -> JobSearchIntent:
        # When the user states a goal, the resume must not silently change that goal.
        # Profile evidence is only used here to infer intent in profile-only searches.
        profile_context = self._profile_context(profile) if not preference.strip() else ""
        text = await self.llm.generate_text(
            system_prompt=(
                "You plan retrieval for a job search. Return strict JSON only. "
                "Separate hard constraints from semantic preferences. Do not treat current residence as a desired "
                "job location. When no explicit preference exists, infer suitable role families from delivered "
                "projects and work evidence, not merely from a skill list. Keep queries concise and complementary. "
                "Every natural-language constraint must quote evidence verbatim from the user preference."
            ),
            user_prompt=(
                "Return this schema: {\"retrieval_query\": string, \"query_variants\": [string], "
                "\"locations\": [{\"value\": string, \"evidence\": string}], "
                "\"excluded_terms\": [{\"value\": string, \"evidence\": string}]}. "
                "Generate 1 primary query and at most 3 total variants. "
                "Do not put locations or negative constraints into retrieval queries. Preserve the user's role "
                "direction; semantic expansion is allowed, invented experience is not.\n\n"
                f"User preference:\n{preference or '(not provided)'}\n\n"
                f"Grounded profile evidence:\n{profile_context or '(not provided)'}\n\n"
                f"Deterministic fallback:\n{fallback.as_dict()}"
            ),
            temperature=0,
            max_tokens=500,
            response_format={"type": "json_object"},
            db=db,
            trace_name="job_discovery.query_planning",
        )
        payload = extract_json_object(text)
        primary = self._bounded_text(payload.get("retrieval_query"), fallback.retrieval_query)
        variants = self._unique(
            [primary, *[self._bounded_text(item, "") for item in payload.get("query_variants") or []]]
        )[:3]
        parsed_locations = self._grounded_values(payload.get("locations"), preference)
        exclusions = self._grounded_values(payload.get("excluded_terms"), preference)
        return JobSearchIntent(
            retrieval_query=primary,
            query_variants=variants or fallback.query_variants,
            locations=fallback.locations or parsed_locations,
            excluded_terms=exclusions,
            planner_mode="llm_grounded",
            profile_inference_used=not bool(preference.strip()) and bool(profile),
        )

    def _profile_queries(self, profile: Profile | None) -> list[str]:
        if not profile:
            return []
        data = profile.structured_profile_json or {}
        roles = self._unique([
            *[str(item) for item in profile.target_roles_json or []],
            str(profile.headline or ""),
        ])
        delivery: list[str] = []
        for section_name in ("projects", "work_experience", "campus_experience"):
            for item in (data.get(section_name) or [])[:3]:
                if not isinstance(item, dict):
                    continue
                delivery.append(" ".join(
                    str(item.get(key) or "") for key in ("name", "role", "description", "details", "impact")
                ).strip())
                delivery.extend(str(skill) for skill in item.get("tech_stack") or [])
        skills = [str(item) for item in data.get("skills") or []]
        role_query = " ".join(roles[:3]).strip()
        evidence_query = " ".join(item for item in delivery if item).strip()[:320]
        capability_query = " ".join(skills[:12]).strip()
        # These are evidence views, not a hard-coded role classification. The job corpus
        # determines which role families are semantically close to the candidate.
        # An explicitly stated target role remains the primary query. Without one,
        # delivered project/work evidence becomes primary and discovers the role family.
        return self._unique(
            [role_query, evidence_query, capability_query]
            if role_query
            else [evidence_query, capability_query]
        )[:3]

    def _profile_context(self, profile: Profile | None) -> str:
        if not profile:
            return ""
        return "\n".join(self._profile_queries(profile))[:1200]

    def _grounded_values(self, rows: Any, source: str) -> list[str]:
        grounded: list[str] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            value = " ".join(str(row.get("value") or "").split()).strip()
            evidence = " ".join(str(row.get("evidence") or "").split()).strip()
            if value and evidence and evidence.lower() in source.lower():
                grounded.append(value)
        return self._unique(grounded)[:8]

    def _text_segments(self, text: str) -> list[str]:
        normalized = " ".join((text or "").split()).strip()
        if not normalized:
            return []
        # Punctuation is document structure, not a vocabulary-dependent intent rule.
        for delimiter in ("。", "；", ";", "，", ","):
            normalized = normalized.replace(delimiter, "\n")
        return self._unique(normalized.splitlines())[:3]

    def _split_explicit_values(self, value: str | None) -> list[str]:
        text = str(value or "").strip()
        for delimiter in ("、", "/", "，", ",", "；", ";"):
            text = text.replace(delimiter, "\n")
        return self._unique(text.splitlines())

    @staticmethod
    def _bounded_text(value: Any, fallback: str) -> str:
        text = " ".join(str(value or "").split()).strip()
        return text[:240] or fallback

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = " ".join(str(value or "").split()).strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                output.append(text)
        return output
