import asyncio
from datetime import datetime, timezone

import pytest

from app.core.llm import LLMClient, LLMResponseError
from app.core.retry import full_jitter_delay, parse_retry_after
from app.core.config import Settings


def test_full_jitter_uses_exponential_cap(monkeypatch):
    monkeypatch.setattr("app.core.retry.random.uniform", lambda low, high: high)

    assert full_jitter_delay(base_seconds=0.5, retry_number=1, max_seconds=5) == 0.5
    assert full_jitter_delay(base_seconds=0.5, retry_number=3, max_seconds=5) == 2.0
    assert full_jitter_delay(base_seconds=2, retry_number=4, max_seconds=5) == 5.0


def test_retry_after_supports_seconds_and_http_date():
    now = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)

    assert parse_retry_after("3.5", now=now) == 3.5
    assert parse_retry_after("Sat, 05 Sep 2026 10:00:07 GMT", now=now) == 7.0
    assert parse_retry_after("not-a-delay", now=now) is None


def test_heuristic_llm_fallback_is_test_harness_only():
    assert Settings(app_env="test", llm_fallback_enabled=True).llm_fallback_enabled is True
    assert Settings(app_env="production", llm_fallback_enabled=True).llm_fallback_enabled is False


def test_llm_429_honors_retry_after_and_records_policy(monkeypatch, db_session):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("LLM_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv("LLM_RETRY_MAX_BACKOFF_SECONDS", "8")
    from app.core.config import get_settings
    from app.models.entities import LLMCallLog

    get_settings.cache_clear()

    class FakeResponse:
        def __init__(self, status_code):
            self.status_code = status_code
            self.text = "rate limited" if status_code == 429 else ""
            self.headers = {"Retry-After": "2"} if status_code == 429 else {}

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    class FakeAsyncClient:
        calls = 0

        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            self.__class__.calls += 1
            return FakeResponse(429 if self.__class__.calls == 1 else 200)

    waits = []

    async def fake_sleep(delay):
        waits.append(delay)

    monkeypatch.setattr("app.core.llm.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.core.llm.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("app.core.retry.random.uniform", lambda _low, _high: 0.1)

    result = asyncio.run(
        LLMClient().generate_text(
            system_prompt="system",
            user_prompt="user",
            db=db_session,
            trace_name="unit_test.retry_after",
        )
    )

    rows = db_session.query(LLMCallLog).filter(LLMCallLog.trace_name == "unit_test.retry_after").all()
    assert result == "ok"
    assert FakeAsyncClient.calls == 2
    assert waits == [2.0]
    assert rows[0].prompt_preview_json["retry_reason"] == "http_429"
    assert rows[0].prompt_preview_json["retry_strategy"] == "capped_exponential_full_jitter"
    get_settings.cache_clear()


def test_llm_client_does_not_retry_conflict_or_client_errors(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("LLM_RETRY_ATTEMPTS", "3")
    from app.core.config import get_settings

    get_settings.cache_clear()

    class ConflictResponse:
        status_code = 409
        text = "conflict"
        headers = {}

    class FakeAsyncClient:
        calls = 0

        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            self.__class__.calls += 1
            return ConflictResponse()

    monkeypatch.setattr("app.core.llm.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(LLMResponseError, match="HTTP 409"):
        asyncio.run(LLMClient().generate_text(system_prompt="system", user_prompt="user"))
    assert FakeAsyncClient.calls == 1
    get_settings.cache_clear()


def test_llm_client_does_not_retry_before_provider_retry_after_budget(monkeypatch, db_session):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("LLM_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("LLM_RETRY_MAX_BACKOFF_SECONDS", "5")
    from app.core.config import get_settings
    from app.models.entities import LLMCallLog

    get_settings.cache_clear()

    class RateLimitedResponse:
        status_code = 429
        text = "rate limited"
        headers = {"Retry-After": "60"}

    class FakeAsyncClient:
        calls = 0

        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            self.__class__.calls += 1
            return RateLimitedResponse()

    monkeypatch.setattr("app.core.llm.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(LLMResponseError, match="HTTP 429"):
        asyncio.run(
            LLMClient().generate_text(
                system_prompt="system",
                user_prompt="user",
                db=db_session,
                trace_name="unit_test.retry_after_budget",
            )
        )

    row = db_session.query(LLMCallLog).filter(
        LLMCallLog.trace_name == "unit_test.retry_after_budget"
    ).one()
    assert FakeAsyncClient.calls == 1
    assert row.prompt_preview_json["retry_suppressed_reason"] == "retry_after_exceeds_latency_budget"
    get_settings.cache_clear()
