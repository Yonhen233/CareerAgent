import json
import re
from typing import Any

import httpx

from app.core.config import get_settings


class LLMConfigurationError(RuntimeError):
    """Raised when an online LLM call is requested without configuration."""


class LLMResponseError(RuntimeError):
    """Raised when the LLM endpoint returns an unusable response."""


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
    ) -> str:
        if not self.available:
            raise LLMConfigurationError("LLM_API_KEY and LLM_BASE_URL are required for online generation.")

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
        return content.strip()

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        text = await self.generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
        )
        return extract_json_object(text)
