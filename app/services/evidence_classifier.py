from __future__ import annotations

from dataclasses import dataclass
from typing import Any


POSITIVE_DELIVERY_CUES = [
    "built",
    "implemented",
    "created",
    "developed",
    "designed",
    "deployed",
    "maintained",
    "delivered",
    "trained",
    "launched",
    "owned",
    "构建",
    "实现",
    "开发",
    "部署",
    "维护",
    "交付",
]

METRIC_CUES = [
    "%",
    "metric",
    "metrics",
    "dashboard",
    "dashboards",
    "evaluation",
    "latency",
    "accuracy",
    "recall",
    "precision",
    "a/b",
    "experiment",
    "reduced",
    "improved",
    "measured",
    "指标",
    "评测",
    "实验",
    "看板",
]

COURSEWORK_CUES = [
    "coursework",
    "course notes",
    "homework",
    "class project",
    "tutorial",
    "read articles",
    "read papers",
    "paper reading",
    "课程",
    "作业",
    "阅读",
]

PLANNED_LEARNING_CUES = [
    "planned",
    "planning to",
    "currently learning",
    "learning about",
    "eager to learn",
    "willing to learn",
    "seeking to learn",
    "计划学习",
    "希望学习",
]

MISSING_DISCLOSURE_CUES = [
    "no ",
    "did not",
    "do not",
    "does not",
    "not implement",
    "not implemented",
    "not build",
    "not built",
    "without ",
    "lacks ",
    "currently lack",
    "no direct",
    "没有",
    "未实现",
    "未交付",
    "缺少",
]


@dataclass(frozen=True)
class EvidenceClassification:
    evidence_type: str
    polarity: str
    confidence: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_type": self.evidence_type,
            "polarity": self.polarity,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
        }


class EvidenceClassifier:
    """Rule-based evidence classifier for resume/JD RAG chunks.

    The classifier is intentionally deterministic: it gives the Agent a stable
    development-time signal before we have enough labeled data for a model-based
    verifier.
    """

    def classify(self, text: str, *, chunk_type: str | None = None, source: str | None = None) -> EvidenceClassification:
        lowered = (text or "").lower()
        chunk_type_norm = (chunk_type or "").lower()
        source_norm = (source or "").lower()

        if self._has_any(lowered, MISSING_DISCLOSURE_CUES):
            return EvidenceClassification(
                evidence_type="missing_skill_disclosure",
                polarity="negative",
                confidence=0.96,
                reason="chunk explicitly states missing or not-built experience",
            )
        if self._has_any(lowered, PLANNED_LEARNING_CUES):
            return EvidenceClassification(
                evidence_type="planned_learning",
                polarity="weak",
                confidence=0.9,
                reason="chunk describes future learning intent instead of delivered work",
            )
        if self._has_any(lowered, COURSEWORK_CUES) or chunk_type_norm == "education":
            return EvidenceClassification(
                evidence_type="coursework",
                polarity="weak",
                confidence=0.86,
                reason="chunk comes from coursework, reading, tutorial, or education context",
            )
        if self._has_any(lowered, METRIC_CUES) and self._has_any(lowered, POSITIVE_DELIVERY_CUES):
            return EvidenceClassification(
                evidence_type="metric_evidence",
                polarity="positive",
                confidence=0.86,
                reason="chunk includes delivered work with measurement or evaluation signal",
            )
        if chunk_type_norm in {"project", "experience"} and self._has_any(lowered, POSITIVE_DELIVERY_CUES):
            return EvidenceClassification(
                evidence_type="shipped_project",
                polarity="positive",
                confidence=0.82,
                reason="project or experience chunk describes delivered implementation work",
            )
        if chunk_type_norm == "skill" or source_norm.endswith(".skills"):
            return EvidenceClassification(
                evidence_type="generic_skill",
                polarity="neutral",
                confidence=0.72,
                reason="standalone skill mention without delivery context",
            )
        if chunk_type_norm in {"project", "experience"}:
            return EvidenceClassification(
                evidence_type="adjacent_experience",
                polarity="neutral",
                confidence=0.66,
                reason="project or experience chunk is related but lacks explicit delivery or metric cues",
            )
        return EvidenceClassification(
            evidence_type="unknown",
            polarity="neutral",
            confidence=0.5,
            reason="no strong evidence-type cue matched",
        )

    def classify_dict(self, item: dict[str, Any]) -> dict[str, Any]:
        classification = self.classify(
            str(item.get("text") or ""),
            chunk_type=str(item.get("chunk_type") or ""),
            source=str(item.get("source") or ""),
        ).as_dict()
        enriched = dict(item)
        enriched.update(classification)
        metadata = dict(enriched.get("metadata") or {})
        metadata["evidence_classification"] = classification
        enriched["metadata"] = metadata
        return enriched

    def _has_any(self, text: str, cues: list[str]) -> bool:
        return any(cue in text for cue in cues)
