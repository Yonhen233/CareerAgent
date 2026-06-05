import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    uid: str
    text: str
    chunk_type: str
    source: str


class ResumeTextSplitter:
    def __init__(self, chunk_size: int = 900, chunk_overlap: int = 160) -> None:
        self.chunk_size = max(240, chunk_size)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_size // 2))

    def split_raw_text(self, text: str, *, prefix: str = "raw") -> list[TextChunk]:
        normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
        if not normalized:
            return []

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", normalized) if p.strip()]
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
            if len(candidate) <= self.chunk_size:
                current = candidate
                continue
            if current:
                chunks.append(current)
            if len(paragraph) <= self.chunk_size:
                current = paragraph
            else:
                chunks.extend(self._window_split(paragraph))
                current = ""
        if current:
            chunks.append(current)

        return [
            TextChunk(
                uid=f"{prefix}_{idx}",
                text=chunk,
                chunk_type="raw_text",
                source="profile.raw_resume_text",
            )
            for idx, chunk in enumerate(chunks)
        ]

    def _window_split(self, text: str) -> list[str]:
        step = max(self.chunk_size - self.chunk_overlap, 1)
        pieces: list[str] = []
        for start in range(0, len(text), step):
            piece = text[start : start + self.chunk_size].strip()
            if piece:
                pieces.append(piece)
            if start + self.chunk_size >= len(text):
                break
        return pieces

    def split_structured_profile(self, profile: dict, *, prefix: str = "structured") -> list[TextChunk]:
        chunks: list[TextChunk] = []
        for idx, skill in enumerate(profile.get("skills", []) or []):
            text = str(skill).strip()
            if text:
                chunks.append(TextChunk(f"{prefix}_skill_{idx}", text, "skill", "profile.skills"))

        for idx, project in enumerate(profile.get("projects", []) or []):
            text = self._flatten_mapping(project)
            if text:
                chunks.append(TextChunk(f"{prefix}_project_{idx}", text, "project", "profile.projects"))

        for idx, exp in enumerate(profile.get("work_experience", []) or []):
            text = self._flatten_mapping(exp)
            if text:
                chunks.append(TextChunk(f"{prefix}_experience_{idx}", text, "experience", "profile.work_experience"))

        for idx, edu in enumerate(profile.get("education", []) or []):
            text = self._flatten_mapping(edu)
            if text:
                chunks.append(TextChunk(f"{prefix}_education_{idx}", text, "education", "profile.education"))

        return chunks

    def build_resume_chunks(self, profile: dict) -> list[TextChunk]:
        chunks = self.split_structured_profile(profile)
        chunks.extend(self.split_raw_text(str(profile.get("raw_text") or "")))
        return chunks

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
