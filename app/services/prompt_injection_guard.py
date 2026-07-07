from __future__ import annotations

import re
from app.core.config import get_settings

from typing import Literal

from pydantic import BaseModel, Field


class PromptInjectionResult(BaseModel):
    detected: bool
    severity: Literal["low", "medium", "high"] = "low"
    categories: list[str] = Field(default_factory=list)
    matched_patterns: list[str] = Field(default_factory=list)
    classifier_score: float | None = None
    classifier_features: list[str] = Field(default_factory=list)
    explanation: str = ""


class PromptInjectionClassifier:
    FEATURE_PATTERNS: dict[str, tuple[str, float, list[str]]] = {
        "override_intent": (
            r"(ignore|forget|disregard|override|bypass|不要遵守|忽略|无视|覆盖).{0,32}(instruction|prompt|system|developer|规则|指令|提示)",
            0.5,
            ["instruction_override"],
        ),
        "forced_output": (
            r"(only output|直接输出|只输出|回答为|say pass|打满分|标记为合格)",
            0.25,
            ["instruction_override"],
        ),
        "tool_command": (
            r"(call|invoke|execute|submit|send|upload|调用|执行|提交|投递|发送|上传).{0,36}(tool|browser|email|resume|profile|简历|工具|邮件|表单|材料|资料)",
            0.5,
            ["tool_escalation"],
        ),
        "data_exfiltration": (
            r"(reveal|print|export|leak|dump|泄露|导出|输出).{0,36}(system prompt|hidden instruction|user data|developer message|系统提示词|隐藏指令|用户数据)",
            0.55,
            ["data_exfiltration"],
        ),
        "retrieval_trigger": (
            r"(when retrieved|retrieved context|如果你读到|作为检索结果|被检索到时)",
            0.45,
            ["rag_pollution"],
        ),
        "external_endpoint": (
            r"(webhook|attacker|evil\.|外部联系方式|外部邮箱|第三方地址)",
            0.25,
            ["tool_escalation", "data_exfiltration"],
        ),
    }

    def predict(self, text: str) -> tuple[float, list[str], list[str]]:
        score = 0.0
        features: list[str] = []
        categories: list[str] = []
        for feature_name, (pattern, weight, feature_categories) in self.FEATURE_PATTERNS.items():
            if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
                score += weight
                features.append(feature_name)
                categories.extend(feature_categories)
        return min(round(score, 4), 1.0), sorted(set(features)), sorted(set(categories))


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

    def __init__(self) -> None:
        self.settings = get_settings()
        self.classifier = PromptInjectionClassifier()

    def detect(self, text: str, source: str = "unknown") -> PromptInjectionResult:
        value = str(text or "")
        categories: list[str] = []
        matched: list[str] = []
        for category, patterns in self.PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, value, flags=re.IGNORECASE):
                    categories.append(category)
                    matched.append(pattern)
        classifier_score, classifier_features, classifier_categories = self.classifier.predict(value)
        if (
            self.settings.prompt_injection_classifier_enabled
            and classifier_score >= self.settings.prompt_injection_classifier_threshold
        ):
            categories.extend(classifier_categories)
            matched.append(f"classifier_score>={self.settings.prompt_injection_classifier_threshold}")
        categories = sorted(set(categories))
        matched = sorted(set(matched))
        if not categories:
            return PromptInjectionResult(
                detected=False,
                classifier_score=classifier_score,
                classifier_features=classifier_features,
                explanation=f"No prompt injection pattern detected in {source}.",
            )
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
            classifier_score=classifier_score,
            classifier_features=classifier_features,
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
            if result.classifier_features and self.detect(line, source=source).detected:
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
