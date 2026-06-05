import httpx

from app.core.llm import format_exception


def test_format_exception_keeps_exception_type_when_message_is_empty():
    message = format_exception(httpx.ReadTimeout(""))

    assert "ReadTimeout" in message
