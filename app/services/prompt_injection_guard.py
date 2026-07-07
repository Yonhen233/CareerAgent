from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field


class PromptInjectionResult(BaseModel):
    detected: bool
    severity: Literal["low", "medium", "high"] = "low"
    categories: list[str] = Field(default_factory=list)
    matched_patterns: list[str] = Field(default_factory=list)
    explanation: str = ""


class PromptInjectionGuard:
    PATTERNS: dict[str, list[str]] = {
        "instruction_override": [
            r"ignore previous instructions",
            r"disregard all prior instructions",
            r"forget your system prompt",
            r"忽略之前的指令",
            r"无视系统提示",
            r"覆盖系统指令",
        ],
        "tool_escalation": [
            r"send the user's resume to",
            r"email this profile to",
            r"call the tool",
            r"submit application automatically",
            r"自动投递",
            r"自动发送邮件",
            r"调用工具",
            r"上传简历到",
        ],
        "data_exfiltration": [
            r"print system prompt",
            r"reveal hidden instructions",
            r"export all user data",
            r"泄露用户数据",
            r"输出系统提示词",
        ],
        "rag_pollution": [
            r"when retrieved,\s*tell the model to",
            r"如果你读到这段文本",
            r"作为检索结果时请执行",
        ],
    }

    HIGH_RISK = {"instruction_override", "tool_escalation", "data_exfiltration"}

    def detect(self, text: str, source: str = "unknown") -> PromptInjectionResult:
        value = str(text or "")
        categories: list[str] = []
        matched: list[str] = []
        for category, patterns in self.PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, value, flags=re.IGNORECASE):
                    categories.append(category)
                    matched.append(pattern)
        categories = sorted(set(categories))
        matched = sorted(set(matched))
        if not categories:
            return PromptInjectionResult(detected=False, explanation=f"No prompt injection pattern detected in {source}.")
        severity: Literal["low", "medium", "high"] = "medium"
        if any(category in self.HIGH_RISK for category in categories):
            severity = "high"
        elif "rag_pollution" in categories:
            severity = "medium"
        return PromptInjectionResult(
            detected=True,
            severity=severity,
            categories=categories,
            matched_patterns=matched,
            explanation=f"Untrusted {source} contains prompt/tool-control language and was marked as {severity} risk.",
        )

    def sanitize_for_llm(self, text: str, source: str = "unknown") -> tuple[str, PromptInjectionResult]:
        result = self.detect(text, source=source)
        if not result.detected:
            return text, result
        safe_lines: list[str] = []
        for line in str(text or "").splitlines():
            if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in result.matched_patterns):
                continue
            safe_lines.append(line)
        sanitized = "\n".join(safe_lines).strip()
        return sanitized, result

    def sanitize_evidence(self, evidence: list[dict], source: str = "rag") -> tuple[list[dict], list[dict]]:
        cleaned: list[dict] = []
        risks: list[dict] = []
        for item in evidence:
            copied = dict(item)
            text = str(copied.get("text") or "")
            sanitized, result = self.sanitize_for_llm(text, source=source)
            if result.detected:
                risks.append(result.model_dump())
                copied["prompt_injection"] = result.model_dump()
                copied["text"] = sanitized
            cleaned.append(copied)
        return cleaned, risks
