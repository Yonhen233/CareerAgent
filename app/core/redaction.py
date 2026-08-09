from __future__ import annotations

import re
from typing import Any


class SecurityRedactor:
    """Redact credentials and common PII from diagnostic payloads."""

    REDACTED = "[REDACTED]"
    _SENSITIVE_KEYS = {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "llm_api_key",
        "openai_api_key",
        "password",
        "refresh_token",
        "session_token",
        "set_cookie",
        "smtp_password",
        "token",
        "x_admin_token",
    }
    _SECRET_PATTERNS = (
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/=]+", re.IGNORECASE),
        re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    )
    _EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
    _PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")

    def redact(self, value: Any, *, redact_pii: bool = False) -> Any:
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                normalized = str(key).strip().lower().replace("-", "_")
                redacted[str(key)] = (
                    self.REDACTED
                    if normalized in self._SENSITIVE_KEYS
                    else self.redact(item, redact_pii=redact_pii)
                )
            return redacted
        if isinstance(value, (list, tuple, set)):
            return [self.redact(item, redact_pii=redact_pii) for item in value]
        if isinstance(value, str):
            text = value
            for pattern in self._SECRET_PATTERNS:
                text = pattern.sub(self.REDACTED, text)
            if redact_pii:
                text = self._EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
                text = self._PHONE_PATTERN.sub("[REDACTED_PHONE]", text)
            return text
        return value

