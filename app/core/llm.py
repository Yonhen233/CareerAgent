import asyncio
import json
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from typing import Any

import httpx

from app.core.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class LLMConfigurationError(RuntimeError):
    """Raised when an online LLM call is requested without configuration."""


class LLMResponseError(RuntimeError):
    """Raised when the LLM endpoint returns an unusable response."""


class LLMBudgetExceededError(RuntimeError):
    """Raised before an LLM call would exceed the active workflow budget."""


class _RetryableLLMResponseError(LLMResponseError):
    """Raised for transient LLM HTTP responses that are worth retrying."""


_LLM_TRACE_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("llm_trace_context", default={})
_LLM_CALL_BUDGET: ContextVar["LLMCallBudget | None"] = ContextVar("llm_call_budget", default=None)


@dataclass
class LLMCallBudget:
    name: str
    max_calls: int
    max_prompt_chars: int
    max_completion_tokens: int
    calls: int = 0
    prompt_chars: int = 0
    reserved_completion_tokens: int = 0
    actual_prompt_tokens: int = 0
    actual_completion_tokens: int = 0
    actual_total_tokens: int = 0
    traces: list[str] = field(default_factory=list)

    def reserve(self, *, trace_name: str, prompt_chars: int, max_tokens: int | None) -> None:
        completion_tokens = max(0, int(max_tokens or 0))
        if self.calls + 1 > self.max_calls:
            raise LLMBudgetExceededError(
                f"LLM budget {self.name} exceeds max_calls={self.max_calls} before {trace_name}."
            )
        if self.prompt_chars + prompt_chars > self.max_prompt_chars:
            raise LLMBudgetExceededError(
                f"LLM budget {self.name} exceeds max_prompt_chars={self.max_prompt_chars} before {trace_name}."
            )
        if self.reserved_completion_tokens + completion_tokens > self.max_completion_tokens:
            raise LLMBudgetExceededError(
                "LLM budget "
                f"{self.name} exceeds max_completion_tokens={self.max_completion_tokens} before {trace_name}."
            )
        self.calls += 1
        self.prompt_chars += prompt_chars
        self.reserved_completion_tokens += completion_tokens
        self.traces.append(trace_name)

    def record_usage(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> None:
        self.actual_prompt_tokens += max(0, prompt_tokens)
        self.actual_completion_tokens += max(0, completion_tokens)
        self.actual_total_tokens += max(0, total_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "limits": {
                "max_calls": self.max_calls,
                "max_prompt_chars": self.max_prompt_chars,
                "max_completion_tokens": self.max_completion_tokens,
            },
            "reserved": {
                "calls": self.calls,
                "prompt_chars": self.prompt_chars,
                "completion_tokens": self.reserved_completion_tokens,
            },
            "actual": {
                "prompt_tokens": self.actual_prompt_tokens,
                "completion_tokens": self.actual_completion_tokens,
                "total_tokens": self.actual_total_tokens,
            },
            "traces": list(self.traces),
        }


@contextmanager
def llm_trace_context(**metadata: Any):
    parent = dict(_LLM_TRACE_CONTEXT.get() or {})
    clean = {key: value for key, value in metadata.items() if value is not None}
    token = _LLM_TRACE_CONTEXT.set({**parent, **clean})
    try:
        yield
    finally:
        _LLM_TRACE_CONTEXT.reset(token)


@contextmanager
def llm_call_budget(budget: LLMCallBudget):
    token = _LLM_CALL_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _LLM_CALL_BUDGET.reset(token)


def format_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return f"{exc.__class__.__name__}: {repr(exc)}"


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise LLMResponseError("LLM response did not contain a JSON object.")

    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise LLMResponseError("LLM JSON response must be an object.")
    return parsed


class LLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def available(self) -> bool:
        return bool(self.settings.effective_llm_api_key and self.settings.effective_llm_base_url)

    def _chat_url(self) -> str:
        base = self.settings.effective_llm_base_url.rstrip("/")
        if not base:
            raise LLMConfigurationError("LLM_BASE_URL is not configured.")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        db: "Session | None" = None,
        trace_name: str = "llm.generate_text",
    ) -> str:
        started = time.perf_counter()
        prompt_preview = self._prompt_preview(system_prompt, user_prompt, temperature, max_tokens, response_format)
        if not self.available:
            error = "LLM_API_KEY and LLM_BASE_URL are required for online generation."
            self._record_llm_call(
                db,
                trace_name=trace_name,
                status="configuration_error",
                prompt_preview=prompt_preview,
                response_preview=None,
                error_message=error,
                started_at=started,
            )
            raise LLMConfigurationError(error)

        active_budget = _LLM_CALL_BUDGET.get()
        prompt_chars = int(prompt_preview.get("system_chars", 0)) + int(
            prompt_preview.get("user_chars", 0)
        )
        headers = {
            "Authorization": f"Bearer {self.settings.effective_llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        payload.update(self._provider_options())

        max_attempts = max(1, int(self.settings.llm_retry_attempts or 0) + 1)
        for attempt in range(1, max_attempts + 1):
            attempt_preview = {**prompt_preview, "attempt": attempt, "max_attempts": max_attempts}
            if active_budget is not None:
                try:
                    active_budget.reserve(
                        trace_name=f"{trace_name}#attempt{attempt}",
                        prompt_chars=prompt_chars,
                        max_tokens=max_tokens,
                    )
                except LLMBudgetExceededError as exc:
                    self._record_llm_call(
                        db,
                        trace_name=trace_name,
                        status="budget_exceeded",
                        prompt_preview=attempt_preview,
                        response_preview=None,
                        error_message=format_exception(exc),
                        started_at=started,
                    )
                    raise
            try:
                async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                    response = await client.post(self._chat_url(), headers=headers, json=payload)
                if response.status_code >= 400:
                    error = f"LLM request failed with HTTP {response.status_code}: {response.text[:500]}"
                    if response.status_code in {408, 409, 429} or response.status_code >= 500:
                        raise _RetryableLLMResponseError(error)
                    raise LLMResponseError(error)

                body = response.json()
                try:
                    choice = body["choices"][0]
                    message = choice["message"]
                    content = message["content"]
                except (KeyError, IndexError, TypeError) as exc:
                    raise LLMResponseError("LLM response is missing choices[0].message.content.") from exc

                if not isinstance(content, str) or not content.strip():
                    reasoning_content = message.get("reasoning_content") if isinstance(message, dict) else None
                    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
                    reasoning_chars = len(reasoning_content) if isinstance(reasoning_content, str) else 0
                    raise LLMResponseError(
                        "LLM returned empty content "
                        f"(finish_reason={finish_reason}, reasoning_chars={reasoning_chars}, "
                        f"thinking_mode={self.settings.llm_thinking_mode})."
                    )
                content = content.strip()
                usage = body.get("usage") if isinstance(body, dict) else None
                usage = usage if isinstance(usage, dict) else {}
                prompt_tokens = self._usage_int(usage, "prompt_tokens", "input_tokens")
                completion_tokens = self._usage_int(usage, "completion_tokens", "output_tokens")
                total_tokens = self._usage_int(usage, "total_tokens")
                if total_tokens <= 0:
                    total_tokens = prompt_tokens + completion_tokens
                if active_budget is not None:
                    active_budget.record_usage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                    )
                self._record_llm_call(
                    db,
                    trace_name=trace_name,
                    status="completed",
                    prompt_preview=attempt_preview,
                    response_preview=content[:1200],
                    response_chars=len(content),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    error_message=None,
                    started_at=started,
                )
                return content
            except Exception as exc:
                retryable = isinstance(exc, (httpx.TransportError, _RetryableLLMResponseError))
                will_retry = retryable and attempt < max_attempts
                error_message = format_exception(exc)
                self._record_llm_call(
                    db,
                    trace_name=trace_name,
                    status="retryable_failed" if will_retry else "failed",
                    prompt_preview=attempt_preview,
                    response_preview=None,
                    error_message=error_message,
                    started_at=started,
                )
                if will_retry:
                    await asyncio.sleep(max(self.settings.llm_retry_backoff_seconds, 0) * attempt)
                    continue
                raise

        raise LLMResponseError("LLM request exhausted without producing a response.")

    @staticmethod
    def _usage_int(usage: dict[str, Any], *keys: str) -> int:
        for key in keys:
            try:
                value = int(usage.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if value >= 0:
                return value
        return 0

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        db: "Session | None" = None,
        trace_name: str = "llm.generate_json",
    ) -> dict[str, Any]:
        text = await self.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            db=db,
            trace_name=trace_name,
        )
        return extract_json_object(text)

    def _prompt_preview(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "system_preview": system_prompt[:1000],
            "user_preview": user_prompt[:1600],
            "system_chars": len(system_prompt),
            "user_chars": len(user_prompt),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format,
        }

    def _provider_options(self) -> dict[str, Any]:
        mode = (self.settings.llm_thinking_mode or "auto").strip().lower()
        if mode in {"omit", "none", "off"}:
            return {}

        base_url = self.settings.effective_llm_base_url.lower()
        model = self.settings.llm_model.lower()
        is_deepseek_v4 = "api.deepseek.com" in base_url and model.startswith("deepseek-v4")
        if mode == "auto" and not is_deepseek_v4:
            return {}

        if mode == "enabled":
            return {
                "thinking": {"type": "enabled"},
                "reasoning_effort": self.settings.llm_reasoning_effort,
            }
        if mode in {"disabled", "auto"}:
            return {"thinking": {"type": "disabled"}}

        return {}

    def _record_llm_call(
        self,
        db: "Session | None",
        *,
        trace_name: str,
        status: str,
        prompt_preview: dict[str, Any],
        response_preview: str | None,
        error_message: str | None,
        started_at: float,
        response_chars: int | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        if db is None:
            return
        try:
            from app.models.entities import LLMCallLog

            db.add(
                LLMCallLog(
                    trace_name=trace_name,
                    model=self.settings.llm_model,
                    base_url=self.settings.effective_llm_base_url,
                    status=status,
                    prompt_preview_json=prompt_preview,
                    response_preview=response_preview,
                    error_message=error_message,
                    latency_ms=int((time.perf_counter() - started_at) * 1000),
                    prompt_chars=int(prompt_preview.get("system_chars", 0))
                    + int(prompt_preview.get("user_chars", 0)),
                    response_chars=response_chars if response_chars is not None else len(response_preview or ""),
                    prompt_tokens=max(0, int(prompt_tokens or 0)),
                    completion_tokens=max(0, int(completion_tokens or 0)),
                    total_tokens=max(0, int(total_tokens or 0)),
                    context_json=dict(_LLM_TRACE_CONTEXT.get() or {}),
                )
            )
            db.commit()
        except Exception:
            db.rollback()
