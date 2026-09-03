import time
import asyncio
import httpx
import pytest

from app.core.llm import (
    LLMCallBudget,
    LLMBudgetExceededError,
    LLMClient,
    llm_call_budget,
    llm_trace_context,
)
from app.api.llm_debug import list_llm_logs
from app.models.entities import AgentRun, ContextCompressionTrace, LLMCallLog
from app.services.llm_usage import LLMUsageService


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
    assert row.context_json["usage_status"] == "missing"
    assert row.context_json["graph_node"] == "unit_test.llm_debug"


def test_llm_budget_blocks_call_before_limit_is_exceeded():
    budget = LLMCallBudget(
        name="unit-test",
        max_calls=1,
        max_prompt_chars=20,
        max_completion_tokens=10,
    )
    budget.reserve(trace_name="first", prompt_chars=10, max_tokens=5)

    with pytest.raises(LLMBudgetExceededError, match="max_calls=1"):
        budget.reserve(trace_name="second", prompt_chars=1, max_tokens=1)


def test_llm_client_records_provider_token_usage_and_budget(monkeypatch, db_session):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    from app.core.config import get_settings

    get_settings.cache_clear()

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 3,
                    "total_tokens": 14,
                    "prompt_cache_hit_tokens": 7,
                    "prompt_cache_miss_tokens": 4,
                },
            }

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            return FakeResponse()

    monkeypatch.setattr("app.core.llm.httpx.AsyncClient", FakeAsyncClient)
    budget = LLMCallBudget(
        name="usage-test",
        max_calls=2,
        max_prompt_chars=100,
        max_completion_tokens=20,
    )
    client = LLMClient()
    with llm_call_budget(budget):
        result = asyncio.run(
            client.generate_text(
                system_prompt="system",
                user_prompt="user",
                max_tokens=10,
                db=db_session,
                trace_name="unit_test.token_usage",
            )
        )

    row = db_session.query(LLMCallLog).filter(LLMCallLog.trace_name == "unit_test.token_usage").one()
    assert result == "ok"
    assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (11, 3, 14)
    assert row.context_json["provider_usage"] == {
        "prompt_cache_hit_tokens": 7,
        "prompt_cache_miss_tokens": 4,
    }
    assert budget.to_dict()["actual"]["total_tokens"] == 14
    assert budget.to_dict()["reserved"]["calls"] == 1
    get_settings.cache_clear()


def test_llm_call_persists_context_trace_without_faking_missing_usage(monkeypatch, db_session):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("CONTEXT_RUNTIME_V2_ENABLED", "false")
    monkeypatch.setenv("CONTEXT_RUNTIME_V2_SHADOW_MODE", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()

    class FakeContextResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    class FakeContextAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            return FakeContextResponse()

    monkeypatch.setattr("app.core.llm.httpx.AsyncClient", FakeContextAsyncClient)
    run = AgentRun(
        tenant_id="tenant-a",
        user_id="user-a",
        task_type="full_career_flow",
        status="running",
        input_json={},
    )
    db_session.add(run)
    db_session.commit()
    with llm_trace_context(
        run_id=run.id,
        task_type="full_career_flow",
        tenant_id="tenant-a",
        user_id="user-a",
        profile_id=3,
    ):
        asyncio.run(
            LLMClient().generate_text(
                system_prompt="system",
                user_prompt="帮我寻找 Agent 开发实习",
                max_tokens=100,
                db=db_session,
                trace_name="natural_language.plan",
            )
        )

    trace = db_session.query(ContextCompressionTrace).one()
    assert trace.mode == "shadow"
    assert trace.contract_name == "natural_language_planner"
    assert trace.raw_input_tokens > 0
    assert trace.actual_prompt_tokens == 0
    assert trace.actual_total_tokens == 0
    assert trace.trace_json["actual_usage_available"] is False
    get_settings.cache_clear()


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
    assert row.context_json["evaluation_run_id"] == 12
    assert row.context_json["case_name"] == "case_a"
    assert row.context_json["stage"] == "jd_parse"
    assert row.context_json["graph_node"] == "jd_parse"
    assert row.context_json["usage_status"] == "missing"


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


def test_llm_usage_aggregates_provider_tokens_and_reports_missing_usage(db_session):
    db_session.add_all(
        [
            LLMCallLog(
                trace_name="interview.questions",
                model="deepseek-v4-pro",
                base_url="https://api.deepseek.com",
                status="completed",
                prompt_preview_json={},
                response_preview="ok",
                prompt_chars=120,
                response_chars=30,
                prompt_tokens=30,
                completion_tokens=10,
                total_tokens=40,
                latency_ms=100,
                context_json={"workflow": "interview_prep", "workflow_run_id": "run-a"},
            ),
            LLMCallLog(
                trace_name="interview.answers",
                model="deepseek-v4-pro",
                base_url="https://api.deepseek.com",
                status="completed",
                prompt_preview_json={},
                response_preview="ok",
                prompt_chars=80,
                response_chars=20,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                latency_ms=80,
                context_json={"workflow": "interview_prep", "workflow_run_id": "run-a"},
            ),
            LLMCallLog(
                trace_name="resume.tailor",
                model="other-model",
                base_url="http://llm",
                status="failed",
                prompt_preview_json={},
                response_preview=None,
                error_message="timeout",
                prompt_chars=40,
                response_chars=0,
                latency_ms=50,
                context_json={"workflow": "resume_tailor", "workflow_run_id": "run-b"},
            ),
        ]
    )
    db_session.commit()

    payload = LLMUsageService().summarize(db_session, hours=24, workflow="interview_prep")

    assert payload["summary"]["log_count"] == 2
    assert payload["summary"]["completed_calls"] == 2
    assert payload["summary"]["provider_usage_calls"] == 1
    assert payload["summary"]["missing_usage_calls"] == 1
    assert payload["summary"]["usage_coverage_rate"] == 0.5
    assert payload["summary"]["total_tokens"] == 40
    assert payload["by_workflow"][0]["key"] == "interview_prep"
    assert payload["by_workflow_run"][0]["key"] == "run-a"


def test_llm_call_log_has_time_window_query_index():
    index_names = {index.name for index in LLMCallLog.__table__.indexes}

    assert "ix_llm_call_logs_created_at" in index_names


def test_deepseek_v4_official_api_disables_thinking_by_default(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("LLM_THINKING_MODE", "auto")
    from app.core.config import get_settings

    get_settings.cache_clear()
    client = LLMClient()

    assert client._provider_options() == {"thinking": {"type": "disabled"}}
    get_settings.cache_clear()


def test_llm_router_assigns_flash_and_pro_by_trace(monkeypatch):
    monkeypatch.setenv("LLM_ROUTING_ENABLED", "true")
    monkeypatch.setenv("LLM_MODEL", "custom-default")
    monkeypatch.setenv("LLM_FLASH_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LLM_PRO_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("LLM_FLASH_MAX_TOKENS_MULTIPLIER", "1.15")
    from app.core.config import get_settings

    get_settings.cache_clear()
    client = LLMClient()

    planner = client.resolve_route("natural_language.plan")
    resume_review = client.resolve_route("resume_review.enhance_suggestions")
    interview = client.resolve_route("interview_agentic_rag.generate.1")
    claim_verifier = client.resolve_route("evaluation.interview_claim_verifier.1")
    unknown = client.resolve_route("future_workflow.unclassified")

    assert (planner.name, planner.model) == ("flash_economy", "deepseek-v4-flash")
    assert client.effective_max_tokens(1000, planner) == 1150
    assert (resume_review.name, resume_review.model) == ("pro_quality", "deepseek-v4-pro")
    assert (interview.name, interview.model) == ("pro_quality", "deepseek-v4-pro")
    assert (claim_verifier.name, claim_verifier.model) == ("pro_quality", "deepseek-v4-pro")
    assert (unknown.name, unknown.model) == ("configured_default", "custom-default")
    get_settings.cache_clear()


def test_llm_router_sends_and_logs_effective_flash_model(monkeypatch, db_session):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_ROUTING_ENABLED", "true")
    monkeypatch.setenv("LLM_FLASH_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LLM_FLASH_MAX_TOKENS_MULTIPLIER", "1.15")
    from app.core.config import get_settings

    get_settings.cache_clear()
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            captured.update(json)
            return FakeResponse()

    monkeypatch.setattr("app.core.llm.httpx.AsyncClient", FakeAsyncClient)
    budget = LLMCallBudget(
        name="flash-route-test",
        max_calls=1,
        max_prompt_chars=100,
        max_completion_tokens=120,
    )
    with llm_call_budget(budget):
        result = asyncio.run(
            LLMClient().generate_text(
                system_prompt="system",
                user_prompt="user",
                max_tokens=100,
                db=db_session,
                trace_name="natural_language.plan",
            )
        )

    row = db_session.query(LLMCallLog).filter(LLMCallLog.trace_name == "natural_language.plan").one()
    assert result == "ok"
    assert captured["model"] == "deepseek-v4-flash"
    assert captured["max_tokens"] == 115
    assert row.model == "deepseek-v4-flash"
    assert row.context_json["model_route"] == "flash_economy"
    assert row.prompt_preview_json["requested_max_tokens"] == 100
    assert row.prompt_preview_json["max_tokens"] == 115
    assert budget.reserved_completion_tokens == 115
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
    budget = LLMCallBudget(
        name="retry-test",
        max_calls=2,
        max_prompt_chars=100,
        max_completion_tokens=20,
    )

    with llm_call_budget(budget):
        text = asyncio.run(
            client.generate_text(
                system_prompt="system",
                user_prompt="user",
                max_tokens=10,
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
    assert budget.calls == 2
    assert budget.traces == ["unit_test.retry#attempt1", "unit_test.retry#attempt2"]
    get_settings.cache_clear()


def test_llm_budget_blocks_retry_before_second_http_request(monkeypatch, db_session):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_RETRY_ATTEMPTS", "1")
    monkeypatch.setenv("LLM_RETRY_BACKOFF_SECONDS", "0")
    from app.core.config import get_settings

    get_settings.cache_clear()

    class FailingAsyncClient:
        calls = 0

        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            FailingAsyncClient.calls += 1
            raise httpx.ConnectError("temporary disconnect")

    monkeypatch.setattr("app.core.llm.httpx.AsyncClient", FailingAsyncClient)
    budget = LLMCallBudget(
        name="retry-hard-stop",
        max_calls=1,
        max_prompt_chars=100,
        max_completion_tokens=10,
    )

    with llm_call_budget(budget), pytest.raises(LLMBudgetExceededError, match="max_calls=1"):
        asyncio.run(
            LLMClient().generate_text(
                system_prompt="system",
                user_prompt="user",
                max_tokens=10,
                db=db_session,
                trace_name="unit_test.retry_hard_stop",
            )
        )

    assert FailingAsyncClient.calls == 1
    assert budget.calls == 1
    rows = (
        db_session.query(LLMCallLog)
        .filter(LLMCallLog.trace_name == "unit_test.retry_hard_stop")
        .order_by(LLMCallLog.id.asc())
        .all()
    )
    assert [row.status for row in rows] == ["retryable_failed", "budget_exceeded"]
    assert rows[-1].prompt_preview_json["attempt"] == 2
    get_settings.cache_clear()
