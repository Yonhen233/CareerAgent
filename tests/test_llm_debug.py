import time
import asyncio
import httpx

from app.core.llm import LLMClient
from app.core.llm import llm_trace_context
from app.api.llm_debug import list_llm_logs
from app.models.entities import LLMCallLog


def test_llm_call_log_records_debug_metadata(db_session):
    client = LLMClient()
    client._record_llm_call(
        db_session,
        trace_name="unit_test.llm_debug",
        status="completed",
        prompt_preview={
            "system_preview": "system",
            "user_preview": "user",
            "system_chars": 6,
            "user_chars": 4,
            "temperature": 0,
        },
        response_preview="ok",
        error_message=None,
        started_at=time.perf_counter(),
    )

    row = db_session.query(LLMCallLog).filter(LLMCallLog.trace_name == "unit_test.llm_debug").first()
    assert row is not None
    assert row.prompt_chars == 10
    assert row.response_chars == 2
    assert row.context_json == {}


def test_llm_call_log_records_trace_context(db_session):
    client = LLMClient()

    with llm_trace_context(evaluation_run_id=12, case_name="case_a", stage="jd_parse"):
        client._record_llm_call(
            db_session,
            trace_name="unit_test.llm_context",
            status="completed",
            prompt_preview={
                "system_preview": "system",
                "user_preview": "user",
                "system_chars": 6,
                "user_chars": 4,
                "temperature": 0,
            },
            response_preview="ok",
            error_message=None,
            started_at=time.perf_counter(),
        )

    row = db_session.query(LLMCallLog).filter(LLMCallLog.trace_name == "unit_test.llm_context").first()
    assert row is not None
    assert row.context_json == {"evaluation_run_id": 12, "case_name": "case_a", "stage": "jd_parse"}


def test_llm_debug_logs_filter_by_context(db_session):
    rows = [
        LLMCallLog(
            trace_name="run_1_case_a",
            model="m",
            base_url="http://llm",
            status="completed",
            prompt_preview_json={},
            response_preview="ok",
            error_message=None,
            prompt_chars=1,
            response_chars=2,
            context_json={"evaluation_run_id": 1, "case_name": "case_a", "stage": "jd_parse"},
        ),
        LLMCallLog(
            trace_name="run_2_case_a",
            model="m",
            base_url="http://llm",
            status="completed",
            prompt_preview_json={},
            response_preview="ok",
            error_message=None,
            prompt_chars=1,
            response_chars=2,
            context_json={"evaluation_run_id": 2, "case_name": "case_a", "stage": "jd_parse"},
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    payload = list_llm_logs(limit=50, evaluation_run_id=1, case_name="case_a", stage=None, db=db_session)

    assert [row.trace_name for row in payload] == ["run_1_case_a"]
    assert payload[0].context_json["stage"] == "jd_parse"


def test_deepseek_v4_official_api_disables_thinking_by_default(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("LLM_THINKING_MODE", "auto")
    from app.core.config import get_settings

    get_settings.cache_clear()
    client = LLMClient()

    assert client._provider_options() == {"thinking": {"type": "disabled"}}
    get_settings.cache_clear()


def test_non_deepseek_provider_omits_thinking_options(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llmapi.paratera.com")
    monkeypatch.setenv("LLM_MODEL", "DeepSeek-V4-Pro")
    monkeypatch.setenv("LLM_THINKING_MODE", "auto")
    from app.core.config import get_settings

    get_settings.cache_clear()
    client = LLMClient()

    assert client._provider_options() == {}
    get_settings.cache_clear()


def test_openai_compatible_base_url_overrides_default_llm_base(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("BASE_URL", "https://api.deepseek.com")
    from app.core.config import get_settings

    get_settings.cache_clear()
    client = LLMClient()

    assert client.settings.effective_llm_base_url == "https://api.deepseek.com"
    get_settings.cache_clear()


def test_generate_json_uses_json_response_format(monkeypatch):
    client = LLMClient()
    captured = {}

    async def fake_generate_text(**kwargs):
        captured.update(kwargs)
        return '{"ok": true}'

    monkeypatch.setattr(client, "generate_text", fake_generate_text)

    result = asyncio.run(
        client.generate_json(
            system_prompt="Return json.",
            user_prompt="Return json.",
        )
    )

    assert result == {"ok": True}
    assert captured["response_format"] == {"type": "json_object"}


def test_llm_client_retries_transient_transport_errors(monkeypatch, db_session):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("LLM_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv("LLM_RETRY_BACKOFF_SECONDS", "0")
    from app.core.config import get_settings

    get_settings.cache_clear()

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class FakeAsyncClient:
        calls = 0

        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            FakeAsyncClient.calls += 1
            if FakeAsyncClient.calls == 1:
                raise httpx.ConnectError("temporary disconnect")
            return FakeResponse()

    monkeypatch.setattr("app.core.llm.httpx.AsyncClient", FakeAsyncClient)
    client = LLMClient()

    text = asyncio.run(
        client.generate_text(
            system_prompt="system",
            user_prompt="user",
            db=db_session,
            trace_name="unit_test.retry",
        )
    )

    rows = (
        db_session.query(LLMCallLog)
        .filter(LLMCallLog.trace_name == "unit_test.retry")
        .order_by(LLMCallLog.id.asc())
        .all()
    )
    assert text == "ok"
    assert FakeAsyncClient.calls == 2
    assert [row.status for row in rows] == ["retryable_failed", "completed"]
    assert rows[0].prompt_preview_json["attempt"] == 1
    assert rows[1].prompt_preview_json["attempt"] == 2
    get_settings.cache_clear()
