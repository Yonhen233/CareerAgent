import json
import re
import time
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
        db: "Session | None" = None,
        trace_name: str = "llm.generate_text",
    ) -> str:
        started = time.perf_counter()
        prompt_preview = self._prompt_preview(system_prompt, user_prompt, temperature)
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
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds) as client:
                response = await client.post(self._chat_url(), headers=headers, json=payload)
            if response.status_code >= 400:
                raise LLMResponseError(f"LLM request failed with HTTP {response.status_code}: {response.text[:500]}")

            body = response.json()
            try:
                content = body["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMResponseError("LLM response is missing choices[0].message.content.") from exc

            if not isinstance(content, str) or not content.strip():
                raise LLMResponseError("LLM returned empty content.")
            content = content.strip()
            self._record_llm_call(
                db,
                trace_name=trace_name,
                status="completed",
                prompt_preview=prompt_preview,
                response_preview=content[:1200],
                error_message=None,
                started_at=started,
            )
            return content
        except Exception as exc:
            error_message = format_exception(exc)
            self._record_llm_call(
                db,
                trace_name=trace_name,
                status="failed",
                prompt_preview=prompt_preview,
                response_preview=None,
                error_message=error_message,
                started_at=started,
            )
            raise

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        db: "Session | None" = None,
        trace_name: str = "llm.generate_json",
    ) -> dict[str, Any]:
        text = await self.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            db=db,
            trace_name=trace_name,
        )
        return extract_json_object(text)

    def _prompt_preview(self, system_prompt: str, user_prompt: str, temperature: float) -> dict[str, Any]:
        return {
            "system_preview": system_prompt[:1000],
            "user_preview": user_prompt[:1600],
            "system_chars": len(system_prompt),
            "user_chars": len(user_prompt),
            "temperature": temperature,
        }

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
                    response_chars=len(response_preview or ""),
                )
            )
            db.commit()
        except Exception:
            db.rollback()
