from __future__ import annotations

import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Parse Retry-After seconds or an HTTP date into a non-negative delay."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, (target - current).total_seconds())


def full_jitter_delay(
    *,
    base_seconds: float,
    retry_number: int,
    max_seconds: float,
    retry_after_seconds: float | None = None,
) -> float:
    """Return capped exponential backoff with full jitter.

    retry_number is one-based: 1 is the wait before the first retry.
    A provider Retry-After value is treated as a minimum wait while the local
    cap remains the workflow's latency boundary.
    """
    base = max(0.0, float(base_seconds))
    cap = max(0.0, float(max_seconds))
    exponential_cap = min(cap, base * (2 ** max(0, int(retry_number) - 1)))
    jittered = random.uniform(0.0, exponential_cap) if exponential_cap > 0 else 0.0
    provider_delay = max(0.0, float(retry_after_seconds or 0.0))
    return min(cap, max(jittered, provider_delay))
