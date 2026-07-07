from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import Settings, get_settings


class OutboundToolError(RuntimeError):
    pass


@dataclass
class EmailPayload:
    to: str
    subject: str
    body: str
    cc: list[str]
    bcc: list[str]


class EmailOutboundTool:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def create_draft(self, payload: dict[str, Any], *, run_id: int) -> dict[str, Any]:
        email_payload = self._parse_payload(payload)
        message = self._build_message(email_payload)
        target_dir = self.settings.outbound_email_draft_path / str(run_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"draft_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid4().hex[:8]}.eml"
        target.write_text(message.as_string(), encoding="utf-8")
        return {
            "status": "draft_created",
            "draft_path": str(target),
            "to": email_payload.to,
            "cc": email_payload.cc,
            "subject": email_payload.subject,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def send_email(self, payload: dict[str, Any], *, run_id: int) -> dict[str, Any]:
        email_payload = self._parse_payload(payload)
        message = self._build_message(email_payload)
        if not self.settings.smtp_host:
            raise OutboundToolError("SMTP_HOST is required for email_send.")
        if not self.settings.smtp_from_email and not self.settings.smtp_username:
            raise OutboundToolError("SMTP_FROM_EMAIL or SMTP_USERNAME is required for email_send.")
        try:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30) as server:
                if self.settings.smtp_use_tls:
                    server.starttls()
                if self.settings.smtp_username:
                    server.login(self.settings.smtp_username, self.settings.smtp_password or "")
                recipients = [email_payload.to, *email_payload.cc, *email_payload.bcc]
                server.send_message(message, to_addrs=recipients)
        except Exception as exc:  # noqa: BLE001
            raise OutboundToolError(f"SMTP email_send failed: {exc.__class__.__name__}: {exc}") from exc
        return {
            "status": "email_sent",
            "run_id": run_id,
            "to": email_payload.to,
            "cc": email_payload.cc,
            "subject": email_payload.subject,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }

    def _parse_payload(self, payload: dict[str, Any]) -> EmailPayload:
        to = str(payload.get("to") or payload.get("recipient") or "").strip()
        subject = str(payload.get("subject") or "").strip()
        body = str(payload.get("body") or payload.get("message") or "").strip()
        if not to:
            raise OutboundToolError("Email payload requires `to`.")
        if not subject:
            raise OutboundToolError("Email payload requires `subject`.")
        if not body:
            raise OutboundToolError("Email payload requires `body`.")
        return EmailPayload(
            to=to,
            subject=subject,
            body=body,
            cc=[str(item).strip() for item in payload.get("cc", []) if str(item).strip()],
            bcc=[str(item).strip() for item in payload.get("bcc", []) if str(item).strip()],
        )

    def _build_message(self, payload: EmailPayload) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.settings.smtp_from_email or self.settings.smtp_username or "career-agent@example.local"
        message["To"] = payload.to
        if payload.cc:
            message["Cc"] = ", ".join(payload.cc)
        message["Subject"] = payload.subject
        message.set_content(payload.body)
        return message


class BrowserApplyTool:
    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def apply(self, payload: dict[str, Any], *, run_id: int) -> dict[str, Any]:
        url = str(payload.get("url") or payload.get("apply_url") or "").strip()
        fields = payload.get("fields") or {}
        submit_selector = payload.get("submit_selector")
        screenshot_path = payload.get("screenshot_path")
        if not url:
            raise OutboundToolError("browser_apply payload requires `url`.")
        if not isinstance(fields, dict) or not fields:
            raise OutboundToolError("browser_apply payload requires non-empty `fields` selector map.")
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # noqa: BLE001
            raise OutboundToolError("playwright is required for browser_apply. Install playwright and browser binaries.") from exc

        filled_selectors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.settings.browser_apply_headless)
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.settings.browser_apply_timeout_ms)
                for selector, value in fields.items():
                    page.fill(str(selector), str(value), timeout=self.settings.browser_apply_timeout_ms)
                    filled_selectors.append(str(selector))
                if screenshot_path:
                    target = Path(str(screenshot_path))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(target), full_page=True)
                if submit_selector:
                    page.click(str(submit_selector), timeout=self.settings.browser_apply_timeout_ms)
                final_url = page.url
            finally:
                browser.close()
        return {
            "status": "submitted" if submit_selector else "filled",
            "run_id": run_id,
            "url": url,
            "final_url": final_url,
            "filled_selectors": filled_selectors,
            "submitted": bool(submit_selector),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
