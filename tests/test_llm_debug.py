import time

from app.core.llm import LLMClient
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
