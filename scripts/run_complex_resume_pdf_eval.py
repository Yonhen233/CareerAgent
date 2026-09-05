from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean
from typing import Any

from app.core.config import get_settings
from app.services.embedding_service import EmbeddingService, cosine_similarity, expand_query_text, tokenize
from app.services.pdf_extraction import PDFExtractionService
from app.services.reranker import RerankerService
from app.services.text_splitter import PDFPageText, ResumeTextSplitter


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "evals" / "complex_resume_corpus" / "manifest.json"
DEFAULT_OUTPUT = ROOT / "evals" / "results" / "complex_resume_pdf_eval.json"


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _best_similarity(expected: str, actual: str) -> float:
    expected_compact = _compact(expected)
    actual_compact = _compact(actual)
    if not expected_compact or not actual_compact:
        return 0.0
    if expected_compact in actual_compact:
        return 1.0
    window = max(len(expected_compact) + 8, int(len(expected_compact) * 1.35))
    step = max(1, len(expected_compact) // 6)
    candidates = [actual_compact[index : index + window] for index in range(0, len(actual_compact), step)]
    return max((SequenceMatcher(None, expected_compact, item).ratio() for item in candidates), default=0.0)


def _rank(query: str, chunks: list[Any], embedding: EmbeddingService, reranker: RerankerService) -> tuple[list[dict], dict]:
    settings = get_settings()
    query = expand_query_text(query)
    texts = []
    for chunk in chunks:
        context = str((chunk.metadata or {}).get("retrieval_context") or "").strip()
        texts.append(
            chunk.text
            if not context or context in chunk.text
            else f"[简历上下文] {context}\n[当前证据] {chunk.text}"
        )
    batch = embedding.embed_texts([query, *texts])
    query_vector = batch.vectors[0]
    query_tokens = set(tokenize(query))
    candidates = []
    for chunk, vector in zip(chunks, batch.vectors[1:], strict=False):
        vector_score = cosine_similarity(query_vector, vector)
        chunk_tokens = set(tokenize(chunk.text))
        lexical_score = len(query_tokens & chunk_tokens) / max(len(query_tokens), 1)
        score = vector_score * settings.retrieval_vector_weight + lexical_score * settings.retrieval_lexical_weight
        candidates.append(
            {
                "uid": chunk.uid,
                "text": chunk.text,
                "chunk_type": chunk.chunk_type,
                "metadata": chunk.metadata or {},
                "score": score,
                "scores": {
                    "vector_score": vector_score,
                    "lexical_score": lexical_score,
                },
            }
        )
    candidates.sort(key=lambda item: item["score"], reverse=True)
    first_stage = candidates[: min(20, len(candidates))]
    return reranker.rerank_dicts(query, first_stage, top_k=len(first_stage)), batch.info()


def evaluate(manifest_path: Path, *, collapse_layout_blocks: bool = False) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    extractor = PDFExtractionService()
    splitter = ResumeTextSplitter()
    embedding = EmbeddingService()
    reranker = RerankerService()
    case_results = []
    retrieval_rows = []

    for case in manifest:
        pdf_path = ROOT / case["pdf_path"]
        extraction = extractor.extract(filename=pdf_path.name, file_bytes=pdf_path.read_bytes())
        chunk_pages = extraction.pages
        if collapse_layout_blocks:
            chunk_pages = [
                PDFPageText(page_no=page.page_no, text=re.sub(r"\n{2,}", "\n", page.text))
                for page in extraction.pages
            ]
        chunks = splitter.split_pdf_pages(chunk_pages, prefix=case["id"])
        ocr_case = extraction.ocr_page_count > 0
        similarity_floor = 0.72 if ocr_case else 0.96
        section_scores = [_best_similarity(section, extraction.raw_text) for section in case["expected_sections"]]
        fact_scores = [_best_similarity(fact, extraction.raw_text) for fact in case["critical_facts"]]
        page_fact_rows = []
        for expectation in case["retrieval_expectations"]:
            expected = expectation["expected_text"]
            expected_page = int(expectation["expected_page_no"])
            page_scores = {page.page_no: _best_similarity(expected, page.text) for page in extraction.pages}
            best_page = max(page_scores, key=page_scores.get)
            ranked, embedding_info = _rank(expectation["query"], chunks, embedding, reranker)
            relevant_ranks = [
                index
                for index, item in enumerate(ranked, start=1)
                if _best_similarity(expected, item["text"]) >= similarity_floor
            ]
            first_rank = min(relevant_ranks) if relevant_ranks else None
            row = {
                "case_id": case["id"],
                "expectation_id": expectation["id"],
                "query": expectation["query"],
                "expected_text": expected,
                "expected_page_no": expected_page,
                "best_extracted_page_no": best_page,
                "page_provenance_correct": best_page == expected_page,
                "extraction_similarity": round(page_scores.get(expected_page, 0.0), 4),
                "rank": first_rank,
                "recall_at_1": bool(first_rank and first_rank <= 1),
                "recall_at_3": bool(first_rank and first_rank <= 3),
                "recall_at_5": bool(first_rank and first_rank <= 5),
                "reciprocal_rank": round(1 / first_rank, 4) if first_rank else 0.0,
                "top_chunks": [
                    {
                        "uid": item["uid"],
                        "page_no": item["metadata"].get("page_no"),
                        "score": round(float(item["score"]), 4),
                        "relevant_similarity": round(_best_similarity(expected, item["text"]), 4),
                        "preview": item["text"][:180],
                    }
                    for item in ranked[:5]
                ],
                "embedding": embedding_info,
            }
            retrieval_rows.append(row)
            page_fact_rows.append(row)

        expected_ocr_pages = 1 if case["layout"] == "scan_two_column" else (1 if case["layout"] == "mixed_text_scan" else 0)
        case_results.append(
            {
                "case_id": case["id"],
                "layout": case["layout"],
                "page_count_correct": extraction.page_count == case["expected_page_count"],
                "ocr_route_correct": extraction.ocr_page_count == expected_ocr_pages,
                "section_recall": round(sum(score >= similarity_floor for score in section_scores) / len(section_scores), 4),
                "critical_fact_recall": round(sum(score >= similarity_floor for score in fact_scores) / len(fact_scores), 4),
                "minimum_fact_similarity": round(min(fact_scores), 4),
                "character_retention_proxy": round(min(1.0, len(_compact(extraction.raw_text)) / max(case["canonical_character_count"], 1)), 4),
                "chunk_count": len(chunks),
                "page_metadata_coverage": round(sum(bool(chunk.metadata and chunk.metadata.get("page_no")) for chunk in chunks) / max(len(chunks), 1), 4),
                "retrieval_failures": [row["expectation_id"] for row in page_fact_rows if not row["recall_at_3"]],
                "diagnostics": extraction.as_dict(),
            }
        )

    summary = {
        "case_count": len(case_results),
        "query_count": len(retrieval_rows),
        "page_count_accuracy": round(mean(row["page_count_correct"] for row in case_results), 4),
        "ocr_route_accuracy": round(mean(row["ocr_route_correct"] for row in case_results), 4),
        "mean_section_recall": round(mean(row["section_recall"] for row in case_results), 4),
        "mean_critical_fact_recall": round(mean(row["critical_fact_recall"] for row in case_results), 4),
        "mean_character_retention_proxy": round(mean(row["character_retention_proxy"] for row in case_results), 4),
        "page_provenance_accuracy": round(mean(row["page_provenance_correct"] for row in retrieval_rows), 4),
        "retrieval_recall_at_1": round(mean(row["recall_at_1"] for row in retrieval_rows), 4),
        "retrieval_recall_at_3": round(mean(row["recall_at_3"] for row in retrieval_rows), 4),
        "retrieval_recall_at_5": round(mean(row["recall_at_5"] for row in retrieval_rows), 4),
        "retrieval_mrr": round(mean(row["reciprocal_rank"] for row in retrieval_rows), 4),
        "failed_case_count": sum(bool(row["retrieval_failures"]) for row in case_results),
    }
    summary["layout_block_boundaries_preserved"] = not collapse_layout_blocks
    return {"evaluation_type": "complex_resume_pdf_and_chunk", "summary": summary, "cases": case_results, "retrieval": retrieval_rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--collapse-layout-blocks", action="store_true")
    args = parser.parse_args()
    result = evaluate(args.manifest, collapse_layout_blocks=args.collapse_layout_blocks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
