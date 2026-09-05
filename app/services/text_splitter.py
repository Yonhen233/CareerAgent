import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextChunk:
    uid: str
    text: str
    chunk_type: str
    source: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class PDFPageText:
    page_no: int
    text: str


class ResumeTextSplitter:
    def __init__(
        self,
        chunk_size: int = 900,
        chunk_overlap: int = 160,
        cross_page_tail_chars: int = 260,
        cross_page_head_chars: int = 520,
    ) -> None:
        self.chunk_size = max(240, chunk_size)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_size // 2))
        self.cross_page_tail_chars = max(80, cross_page_tail_chars)
        self.cross_page_head_chars = max(120, cross_page_head_chars)

    def split_raw_text(
        self,
        text: str,
        *,
        prefix: str = "raw",
        source: str = "profile.raw_resume_text",
        metadata: dict[str, Any] | None = None,
    ) -> list[TextChunk]:
        normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
        if not normalized:
            return []

        paragraphs = self._paragraphs_with_offsets(normalized)
        chunks: list[tuple[str, int, int]] = []
        current = ""
        current_start = 0
        current_end = 0
        for paragraph, start, end in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= self.chunk_size:
                if not current:
                    current_start = start
                current = candidate
                current_end = end
                continue
            if current:
                chunks.append((current, current_start, current_end))
            if len(paragraph) <= self.chunk_size:
                current = paragraph
                current_start = start
                current_end = end
            else:
                chunks.extend(self._window_split(paragraph, base_offset=start))
                current = ""
                current_start = 0
                current_end = 0
        if current:
            chunks.append((current, current_start, current_end))

        return [
            TextChunk(
                uid=f"{prefix}_{idx}",
                text=chunk,
                chunk_type="raw_text",
                source=source,
                metadata={
                    **(metadata or {}),
                    "char_start": start,
                    "char_end": end,
                    "strategy": "paragraph_then_sliding_window",
                    "chunk_size": self.chunk_size,
                    "chunk_overlap": self.chunk_overlap,
                },
            )
            for idx, (chunk, start, end) in enumerate(chunks)
        ]

    def split_pdf_pages(self, pages: list[PDFPageText], *, prefix: str = "pdf") -> list[TextChunk]:
        chunks: list[TextChunk] = []
        retrieval_context = self._pdf_retrieval_context(pages)
        for page in pages:
            page_chunks = self.split_raw_text(
                page.text,
                prefix=f"{prefix}_page_{page.page_no}",
                source="profile.pdf_page_text",
                metadata={
                    "page_no": page.page_no,
                    "source_format": "pdf",
                    "retrieval_context": retrieval_context,
                    "retrieval_context_version": "pdf_header_v1",
                },
            )
            chunks.extend(page_chunks)
        for previous, current in zip(pages, pages[1:], strict=False):
            previous_text = previous.text.strip()
            current_text = current.text.strip()
            if not previous_text or not current_text or not self._needs_cross_page_bridge(previous_text, current_text):
                continue
            bridge = (
                previous_text[-self.cross_page_tail_chars :]
                + "\n\n[跨页续接]\n\n"
                + current_text[: self.cross_page_head_chars]
            ).strip()
            chunks.append(
                TextChunk(
                    uid=f"{prefix}_cross_page_{previous.page_no}_{current.page_no}",
                    text=bridge,
                    chunk_type="raw_text",
                    source="profile.pdf_cross_page_context",
                    metadata={
                        "page_no": current.page_no,
                        "page_start": previous.page_no,
                        "page_end": current.page_no,
                        "source_format": "pdf",
                        "strategy": "cross_page_semantic_bridge",
                        "retrieval_context": retrieval_context,
                        "retrieval_context_version": "pdf_header_v1",
                    },
                )
            )
        return chunks

    @staticmethod
    def _pdf_retrieval_context(pages: list[PDFPageText]) -> str:
        if not pages:
            return ""
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", pages[0].text) if item.strip()]
        context = " | ".join(paragraphs[:4])
        return " ".join(context.split())[:320]

    @staticmethod
    def _needs_cross_page_bridge(previous_text: str, current_text: str) -> bool:
        previous_lines = [line.strip() for line in previous_text.splitlines() if line.strip()]
        current_lines = [line.strip() for line in current_text.splitlines() if line.strip()]
        if not previous_lines or not current_lines:
            return False

        previous_tail = previous_lines[-1]
        current_head = current_lines[0]
        bullet_prefixes = ("-", "*", "•", "·", "▪", "◦", "●", "○")
        if current_head.startswith(bullet_prefixes):
            return True

        terminal_punctuation = ("。", "！", "？", ".", "!", "?", ";", "；")
        if previous_tail.endswith(terminal_punctuation):
            return False

        # An unclosed tail is either a stranded heading or a sentence split by pagination.
        return True

    def split_jd_text(self, jd_text: str, structured_jd: dict, *, prefix: str = "jd") -> list[TextChunk]:
        chunks: list[TextChunk] = []

        field_map = {
            "required_skills": "jd.required_skills",
            "responsibility_skills": "jd.responsibility_skills",
            "preferred_skills": "jd.preferred_skills",
            "responsibilities": "jd.responsibilities",
            "qualifications": "jd.qualifications",
            "keywords": "jd.keywords",
        }
        for field, source in field_map.items():
            values = [str(item).strip() for item in structured_jd.get(field, []) or [] if str(item).strip()]
            if not values:
                continue
            text = "\n".join(values)
            chunks.append(
                TextChunk(
                    uid=f"{prefix}_{field}",
                    text=text,
                    chunk_type=field,
                    source=source,
                    metadata={"field": field, "strategy": "structured_jd_field"},
                )
            )

        alternative_groups = structured_jd.get("alternative_skill_groups") or []
        alternative_text = "\n".join(
            str(group.get("label") or " / ".join(group.get("skills") or [])).strip()
            for group in alternative_groups
            if isinstance(group, dict)
        ).strip()
        if alternative_text:
            chunks.append(
                TextChunk(
                    uid=f"{prefix}_alternative_skill_groups",
                    text=alternative_text,
                    chunk_type="alternative_skill_groups",
                    source="jd.alternative_skill_groups",
                    metadata={"field": "alternative_skill_groups", "strategy": "structured_jd_field"},
                )
            )

        raw_chunks = self.split_raw_text(
            jd_text,
            prefix=f"{prefix}_raw",
            source="job.raw_jd_text",
            metadata={"strategy": "jd_raw_paragraph_window"},
        )
        for chunk in raw_chunks:
            chunks.append(
                TextChunk(
                    uid=chunk.uid,
                    text=chunk.text,
                    chunk_type="jd_raw_text",
                    source=chunk.source,
                    metadata=chunk.metadata,
                )
            )
        return chunks

    def _paragraphs_with_offsets(self, text: str) -> list[tuple[str, int, int]]:
        results: list[tuple[str, int, int]] = []
        for match in re.finditer(r"\S(?:.*?)(?=\n\s*\n|\Z)", text, flags=re.DOTALL):
            paragraph = match.group(0).strip()
            if paragraph:
                results.append((paragraph, match.start(), match.end()))
        return results

    def _window_split(self, text: str, *, base_offset: int = 0) -> list[tuple[str, int, int]]:
        step = max(self.chunk_size - self.chunk_overlap, 1)
        pieces: list[tuple[str, int, int]] = []
        for start in range(0, len(text), step):
            piece = text[start : start + self.chunk_size].strip()
            if piece:
                pieces.append((piece, base_offset + start, base_offset + start + len(piece)))
            if start + self.chunk_size >= len(text):
                break
        return pieces

    def split_structured_profile(self, profile: dict, *, prefix: str = "structured") -> list[TextChunk]:
        chunks: list[TextChunk] = []
        summary = str(profile.get("self_summary") or "").strip()
        if summary:
            chunks.append(
                TextChunk(
                    f"{prefix}_summary",
                    summary,
                    "summary",
                    "profile.self_summary",
                    {"field": "self_summary", "strategy": "structured_profile_field"},
                )
            )

        for idx, skill in enumerate(profile.get("skills", []) or []):
            text = str(skill).strip()
            if text:
                chunks.append(
                    TextChunk(
                        f"{prefix}_skill_{idx}",
                        text,
                        "skill",
                        "profile.skills",
                        {"field": "skills", "item_index": idx, "strategy": "structured_profile_field"},
                    )
                )

        for idx, project in enumerate(profile.get("projects", []) or []):
            text = self._flatten_mapping(project)
            if text:
                chunks.append(
                    TextChunk(
                        f"{prefix}_project_{idx}",
                        text,
                        "project",
                        "profile.projects",
                        {"field": "projects", "item_index": idx, "strategy": "structured_profile_field"},
                    )
                )

        for idx, exp in enumerate(profile.get("work_experience", []) or []):
            text = self._flatten_mapping(exp)
            if text:
                chunks.append(
                    TextChunk(
                        f"{prefix}_experience_{idx}",
                        text,
                        "experience",
                        "profile.work_experience",
                        {"field": "work_experience", "item_index": idx, "strategy": "structured_profile_field"},
                    )
                )

        for idx, exp in enumerate(profile.get("campus_experience", []) or []):
            text = self._flatten_mapping(exp)
            if text:
                chunks.append(
                    TextChunk(
                        f"{prefix}_campus_experience_{idx}",
                        text,
                        "experience",
                        "profile.campus_experience",
                        {"field": "campus_experience", "item_index": idx, "strategy": "structured_profile_field"},
                    )
                )

        for idx, edu in enumerate(profile.get("education", []) or []):
            text = self._flatten_mapping(edu)
            if text:
                chunks.append(
                    TextChunk(
                        f"{prefix}_education_{idx}",
                        text,
                        "education",
                        "profile.education",
                        {"field": "education", "item_index": idx, "strategy": "structured_profile_field"},
                    )
                )

        list_fields = {
            "certifications": "credential",
            "awards": "award",
            "languages": "language",
            "portfolio_links": "portfolio",
        }
        for field, chunk_type in list_fields.items():
            for idx, item in enumerate(profile.get(field, []) or []):
                text = str(item).strip()
                if text:
                    chunks.append(
                        TextChunk(
                            f"{prefix}_{field}_{idx}",
                            text,
                            chunk_type,
                            f"profile.{field}",
                            {"field": field, "item_index": idx, "strategy": "structured_profile_field"},
                        )
                    )

        return chunks

    def build_resume_chunks(self, profile: dict) -> list[TextChunk]:
        chunks = self.split_structured_profile(profile)
        chunks.extend(self.split_raw_text(self._clean_profile_raw_text(profile)))
        return chunks

    def _clean_profile_raw_text(self, profile: dict) -> str:
        raw_text = str(profile.get("raw_text") or "")
        target_roles = [str(item).strip().lower() for item in profile.get("target_roles", []) or [] if str(item).strip()]
        exact_metadata = {
            str(value).strip().lower()
            for value in [
                profile.get("name"),
                profile.get("email"),
                profile.get("phone"),
                profile.get("headline"),
            ]
            if str(value or "").strip()
        }
        cleaned: list[str] = []
        for line in raw_text.splitlines():
            stripped = line.strip()
            lowered = stripped.lower()
            if not stripped:
                continue
            if lowered in exact_metadata:
                continue
            if lowered.startswith(("target roles:", "target role:", "目标岗位", "求职意向")):
                continue
            if self._is_target_intent_line(lowered, target_roles):
                continue
            cleaned.append(stripped)
        return "\n".join(cleaned)

    def _is_target_intent_line(self, lowered_line: str, target_roles: list[str]) -> bool:
        if not target_roles:
            return False
        intent_cues = ["target", "candidate", "seeking", "applying", "目标", "求职", "候选人", "应聘"]
        return any(role in lowered_line for role in target_roles) and any(cue in lowered_line for cue in intent_cues)

    def _flatten_mapping(self, value: object) -> str:
        if isinstance(value, dict):
            parts: list[str] = []
            for item in value.values():
                if isinstance(item, list):
                    parts.extend(str(x).strip() for x in item if str(x).strip())
                elif str(item).strip():
                    parts.append(str(item).strip())
            return " | ".join(parts)
        return str(value).strip()
