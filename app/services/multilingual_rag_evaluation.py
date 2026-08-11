from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.entities import EvaluationRun
from app.services.embedding_service import EmbeddingService, cosine_similarity, expand_query_text, tokenize
from app.services.evidence_classifier import EvidenceClassifier
from app.services.reranker import RerankerService


class MultilingualRAGEvaluationService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.embedder = EmbeddingService(settings=self.settings)
        self.reranker = RerankerService(settings=self.settings)
        self.evidence_classifier = EvidenceClassifier()

    def run(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        dataset_file = dataset_path or self.settings.base_path / "evals" / "rag_multilingual_calibration.json"
        policy_file = self.settings.base_path / "evals" / "rag_multilingual_release_policy.json"
        payload = json.loads(dataset_file.read_text(encoding="utf-8"))
        policy = json.loads(policy_file.read_text(encoding="utf-8"))
        cases = payload["cases"]
        vectors, embedding_info = self._embed_dataset(cases)
        first_stage, pairs = self._score_cases(cases, vectors)
        reranked = self._rerank_cases(first_stage)
        strategies = {
            "multilingual_vector_only": self._summary(self._rank_by(first_stage, "vector_score")),
            "production_hybrid_first_stage": self._summary(first_stage),
            "production_hybrid_top20_rerank": self._summary(reranked),
        }
        selected_name = max(
            strategies,
            key=lambda name: (
                strategies[name]["recall_at_5"],
                strategies[name]["mrr"],
                strategies[name]["top1_accuracy"],
            ),
        )
        selected = strategies[selected_name]
        calibration = self._calibrate_gate(pairs)
        language_recalls = [row["recall_at_5"] for row in selected["language_pair_breakdown"].values()]
        checks = [
            self._check("case_count", len(cases), ">=", policy["minimum_case_count"]),
            self._check("top1_accuracy", selected["top1_accuracy"], ">=", policy["min_top1_accuracy"]),
            self._check("recall_at_5", selected["recall_at_5"], ">=", policy["min_recall_at_5"]),
            self._check("min_language_pair_recall_at_5", min(language_recalls), ">=", policy["min_language_pair_recall_at_5"]),
            self._check("language_pair_recall_gap", max(language_recalls) - min(language_recalls), "<=", policy["max_language_pair_recall_gap"]),
            self._check("evidence_gate_recall", calibration["selected"]["recall"], ">=", policy["min_evidence_gate_recall"]),
            self._check("evidence_gate_false_positive_rate", calibration["selected"]["false_positive_rate"], "<=", policy["max_evidence_gate_false_positive_rate"]),
        ]
        summary = {
            "evaluation_type": "multilingual_rag_calibration",
            "dataset_version": payload["version"],
            "policy_version": policy["version"],
            "case_count": len(cases),
            "concept_count": len({case["concept_id"] for case in cases}),
            "embedding": embedding_info,
            "configured_reranker": {"provider": self.settings.reranker_provider, "model": self.settings.reranker_model_name},
            "selected_strategy": selected_name,
            "selected_metrics": selected,
            "strategy_results": strategies,
            "evidence_gate_calibration": calibration,
            "release_gate": {"passed": all(row["passed"] for row in checks), "checks": checks},
        }
        run = EvaluationRun(name="multilingual_rag_calibration", summary_json=summary, case_results_json=reranked)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def _embed_dataset(self, cases: list[dict[str, Any]]) -> tuple[dict[str, list[float]], dict[str, Any]]:
        texts: list[str] = []
        seen: set[str] = set()
        for case in cases:
            for text in [expand_query_text(case["query"]), *[row["text"] for row in case["evidence_chunks"]]]:
                if text not in seen:
                    seen.add(text)
                    texts.append(text)
        batch = self.embedder.embed_texts(texts)
        return dict(zip(texts, batch.vectors, strict=False)), batch.info()

    def _score_cases(self, cases: list[dict[str, Any]], vectors: dict[str, list[float]]) -> tuple[list[dict], list[dict]]:
        results, pairs = [], []
        for case in cases:
            query = expand_query_text(case["query"])
            query_tokens = set(tokenize(query))
            ranked = []
            for chunk in case["evidence_chunks"]:
                vector = cosine_similarity(vectors[query], vectors[chunk["text"]])
                lexical = len(query_tokens & set(tokenize(chunk["text"]))) / max(len(query_tokens), 1)
                boost = self.settings.retrieval_type_boost if chunk["chunk_type"] in {"project", "experience", "skill", "required_skills"} else 0.0
                score = vector * self.settings.retrieval_vector_weight + lexical * self.settings.retrieval_lexical_weight + boost
                row = {
                    "uid": chunk["chunk_id"], "text": chunk["text"], "chunk_type": chunk["chunk_type"],
                    "expected": bool(chunk["expected"]), "metadata": {"noise_profile": chunk["noise_profile"]},
                    "score": round(score, 6), "vector_score": round(vector, 6),
                    "lexical_score": round(lexical, 6), "first_stage_score": round(score, 6),
                }
                ranked.append(row)
                classification = self.evidence_classifier.classify(
                    chunk["text"], chunk_type=chunk["chunk_type"]
                )
                pairs.append({"language_pair": case["language_pair"], "expected": row["expected"], "vector_score": vector, "lexical_score": lexical, "first_stage_score": score, "support_eligible": classification.polarity not in {"negative", "weak"} and classification.evidence_type not in {"missing_skill_disclosure", "planned_learning", "coursework"}})
            ranked.sort(key=lambda row: row["score"], reverse=True)
            results.append(self._case_result(case, ranked))
        return results, pairs

    def _rerank_cases(self, cases: list[dict]) -> list[dict]:
        groups = [(case["query"], case["ranked_chunks"], 10) for case in cases]
        reranked_groups = self.reranker.rerank_dict_groups(groups)
        return [self._case_result(case, ranked) for case, ranked in zip(cases, reranked_groups, strict=False)]

    @staticmethod
    def _rank_by(cases: list[dict], key: str) -> list[dict]:
        return [MultilingualRAGEvaluationService._case_result(case, sorted(case["ranked_chunks"], key=lambda row: row[key], reverse=True)) for case in cases]

    @staticmethod
    def _case_result(case: dict, ranked: list[dict]) -> dict:
        expected = {row["uid"] for row in ranked if row.get("expected")}
        first_rank = next((index for index, row in enumerate(ranked, 1) if row["uid"] in expected), None)
        return {
            "name": case["name"], "concept_id": case["concept_id"], "language_pair": case["language_pair"],
            "query_language": case["query_language"], "evidence_language": case["evidence_language"],
            "difficulty": case["difficulty"], "query": case["query"],
            "top1_expected": bool(ranked and ranked[0]["uid"] in expected),
            "recall_at_5": len({row["uid"] for row in ranked[:5]} & expected) / max(len(expected), 1),
            "reciprocal_rank": 1.0 / first_rank if first_rank else 0.0, "ranked_chunks": ranked,
        }

    @staticmethod
    def _summary(cases: list[dict]) -> dict:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for case in cases:
            grouped[case["language_pair"]].append(case)

        def aggregate(rows: list[dict]) -> dict:
            count = max(len(rows), 1)
            return {"case_count": len(rows), "top1_accuracy": round(sum(row["top1_expected"] for row in rows) / count, 4), "recall_at_5": round(sum(row["recall_at_5"] for row in rows) / count, 4), "mrr": round(sum(row["reciprocal_rank"] for row in rows) / count, 4)}

        return {**aggregate(cases), "language_pair_breakdown": {key: aggregate(rows) for key, rows in grouped.items()}}

    def _calibrate_gate(self, rows: list[dict]) -> dict:
        candidates = [self._score_thresholds(rows, vector, lexical, first) for vector in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75) for lexical in (0.10, 0.15, 0.20, 0.25, 0.30, 0.40) for first in (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)]
        feasible = [row for row in candidates if row["meets_constraints"]]
        selected = max(feasible or candidates, key=lambda row: (row["meets_constraints"], row["f1"], -row["false_positive_rate"], min(row["language_pair_recall"].values())))
        legacy = self._score_thresholds(rows, -1.0, 0.08, 0.08, legacy=True)
        configured = self._score_thresholds(
            rows,
            self.settings.rag_min_vector_score,
            self.settings.rag_min_query_coverage,
            self.settings.rag_min_first_stage_score,
        )
        return {"pair_count": len(rows), "positive_count": sum(row["expected"] for row in rows), "negative_count": sum(not row["expected"] for row in rows), "legacy_baseline": legacy, "configured": configured, "selected": selected, "feasible_candidate_count": len(feasible), "selection_rule": "先满足整体召回、误放率和最弱语言桶召回约束，再按 F1 与低误报排序。"}

    @staticmethod
    def _score_thresholds(rows: list[dict], vector: float, lexical: float, first: float, *, legacy: bool = False) -> dict:
        counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
        buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fn": 0})
        for row in rows:
            predicted = (row["lexical_score"] >= lexical or row["first_stage_score"] >= first) if legacy else (row["vector_score"] >= vector or (row["lexical_score"] >= lexical and row["first_stage_score"] >= first))
            if not legacy:
                predicted = predicted and bool(row.get("support_eligible", True))
            expected = row["expected"]
            counts["tp" if predicted and expected else "fp" if predicted else "fn" if expected else "tn"] += 1
            if expected:
                buckets[row["language_pair"]]["tp" if predicted else "fn"] += 1
        recall = counts["tp"] / max(counts["tp"] + counts["fn"], 1)
        precision = counts["tp"] / max(counts["tp"] + counts["fp"], 1)
        fpr = counts["fp"] / max(counts["fp"] + counts["tn"], 1)
        language_recall = {key: round(value["tp"] / max(value["tp"] + value["fn"], 1), 4) for key, value in buckets.items()}
        return {"thresholds": {"min_vector_score": vector, "min_query_coverage": lexical, "min_first_stage_score": first, "policy": "legacy_or" if legacy else "vector_or_lexical_corroborated"}, **counts, "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(2 * precision * recall / max(precision + recall, 1e-12), 4), "false_positive_rate": round(fpr, 4), "language_pair_recall": language_recall, "meets_constraints": recall >= 0.95 and fpr <= 0.10 and min(language_recall.values(), default=0.0) >= 0.90}

    @staticmethod
    def _check(metric: str, actual: float, operator: str, threshold: float) -> dict:
        return {"metric": metric, "actual": actual, "operator": operator, "threshold": threshold, "passed": actual >= threshold if operator == ">=" else actual <= threshold}
