from __future__ import annotations

import re
from collections.abc import Iterable

from app.models.entities import Job


def user_visible_jobs(jobs: Iterable[Job]) -> list[Job]:
    """Remove evaluation fixtures and repeated postings from user-facing results."""

    visible: list[Job] = []
    seen: set[tuple[str, str, str]] = set()
    for job in jobs:
        source = str(job.source or "").strip().lower()
        if source == "eval" or source.startswith("eval_") or source.endswith("_eval"):
            continue
        if _is_placeholder_url(job.apply_url):
            continue
        key = (
            _normalize(job.company),
            _normalize(job.title),
            _normalize(job.location),
        )
        if key in seen:
            continue
        seen.add(key)
        visible.append(job)
    return visible


def _normalize(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _is_placeholder_url(value: object) -> bool:
    url = str(value or "").strip().lower()
    return any(marker in url for marker in ("example.com", "localhost", "127.0.0.1"))
