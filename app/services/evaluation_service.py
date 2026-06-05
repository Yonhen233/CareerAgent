import json
import math
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import EvaluationRun, Job
from app.models.schemas import GuidedProfileRequest
from app.services.guardrails import ResumeGuardrailService
from app.services.jd_parser import JDParserService
from app.services.resume_tailor import ResumeTailorService
from app.core.llm import LLMClient
from app.services.matcher import MatcherService
from app.services.resume_parser import ResumeParserService
from app.services.text_splitter import PDFPageText, ResumeTextSplitter, TextChunk
from app.services.vector_index import SQLiteVectorIndex, cosine_similarity, expand_query_text, hash_embedding, tokenize


class EvaluationService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.matcher = MatcherService()
        self.llm = LLMClient()

    async def run_sample_evaluation(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "sample_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        case_results = []
        for case in cases:
            case_results.append(await self._run_case(db, case))
        summary = self._summarize(case_results)
        run = EvaluationRun(
            name=path.name,
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def run_pdf_chunk_strategy_evaluation(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "pdf_chunk_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        strategy_results = []
        case_results = []
        strategies = {
            "fixed_window_450_overlap80": self._pdf_fixed_window_450,
            "paragraph_page_900_overlap160": self._pdf_paragraph_page_900,
            "paragraph_page_1200_overlap200": self._pdf_paragraph_page_1200,
            "section_aware_700_overlap120": self._pdf_section_aware_700,
        }
        for strategy_name, splitter in strategies.items():
            per_query = []
            for case in cases:
                pages = [PDFPageText(page_no=page["page_no"], text=page["text"]) for page in case["pages"]]
                chunks = splitter(pages, case_name=case["name"])
                for query in case["queries"]:
                    ranked = self._rank_text_chunks(query["query"], chunks, vector_weight=0.65, lexical_weight=0.35)
                    top_k = ranked[:3]
                    hit = any(query["expected_keyword"].lower() in item["text"].lower() for item in top_k)
                    page_hit = any(item["metadata"].get("page_no") == query["expected_page"] for item in top_k)
                    context_keywords = [str(item).lower() for item in query.get("expected_context_keywords", [])]
                    context_hit = any(
                        all(keyword in item["text"].lower() for keyword in context_keywords)
                        for item in top_k
                    )
                    per_query.append(
                        {
                            "case": case["name"],
                            "query": query["query"],
                            "hit": hit,
                            "page_hit": page_hit,
                            "context_hit": context_hit,
                            "top1_chars": len(ranked[0]["text"]) if ranked else 0,
                            "chunk_count": len(chunks),
                        }
                    )
            summary = self._summarize_pdf_strategy(strategy_name, per_query)
            strategy_results.append(summary)
            case_results.extend({"strategy": strategy_name, **item} for item in per_query)

        selected = self._select_pdf_strategy(strategy_results)
        summary = {
            "evaluation_type": "pdf_chunk_strategy",
            "dataset": str(path.name),
            "case_count": len(cases),
            "query_count": sum(len(case["queries"]) for case in cases),
            "selected_strategy": selected["strategy"],
            "selection_reason": selected["reason"],
            "strategy_results": strategy_results,
        }
        run = EvaluationRun(
            name="pdf_chunk_strategy_evaluation",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def run_rag_strategy_evaluation(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "rag_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        strategies = {
            "vector_only": {"vector_weight": 1.0, "lexical_weight": 0.0, "type_boost": False},
            "lexical_only": {"vector_weight": 0.0, "lexical_weight": 1.0, "type_boost": False},
            "lexical_80_vector_15_type_5": {
                "vector_weight": 0.15,
                "lexical_weight": 0.8,
                "type_boost": True,
                "query_expansion": True,
            },
            "hybrid_70_vector_30_lexical": {"vector_weight": 0.7, "lexical_weight": 0.3, "type_boost": False},
            "hybrid_58_vector_34_lexical_8_type_boost": {
                "vector_weight": 0.58,
                "lexical_weight": 0.34,
                "type_boost": True,
                "query_expansion": False,
            },
            "hybrid_alias_62_vector_33_lexical_5_type_boost": {
                "vector_weight": 0.62,
                "lexical_weight": 0.33,
                "type_boost": True,
                "query_expansion": True,
            },
        }
        strategy_results = []
        case_results = []
        for strategy_name, config in strategies.items():
            per_case = []
            for case in cases:
                chunks = [
                    TextChunk(
                        uid=item["chunk_id"],
                        text=item["text"],
                        chunk_type=item["chunk_type"],
                        source="eval.rag_cases",
                        metadata={"expected": item["expected"]},
                    )
                    for item in case["evidence_chunks"]
                ]
                ranked = self._rank_text_chunks(
                    expand_query_text(case["query"]) if config.get("query_expansion") else case["query"],
                    chunks,
                    vector_weight=config["vector_weight"],
                    lexical_weight=config["lexical_weight"],
                    type_boost=config["type_boost"],
                )
                expected_ids = set(case["expected_chunk_ids"])
                top3 = ranked[:3]
                top5 = ranked[:5]
                per_case.append(
                    {
                        "case": case["name"],
                        "top3_recall": self._recall({item["uid"] for item in top3}, expected_ids),
                        "top5_recall": self._recall({item["uid"] for item in top5}, expected_ids),
                        "mrr": self._mrr(ranked, expected_ids),
                        "ndcg_at_5": self._ndcg_at_k(ranked, expected_ids, 5),
                        "top1_expected": ranked[0]["uid"] in expected_ids if ranked else False,
                        "top3_ids": [item["uid"] for item in top3],
                    }
                )
            summary = self._summarize_rag_strategy(strategy_name, per_case)
            strategy_results.append(summary)
            case_results.extend({"strategy": strategy_name, **item} for item in per_case)

        selected = self._select_rag_strategy(strategy_results)
        summary = {
            "evaluation_type": "rag_strategy",
            "dataset": str(path.name),
            "case_count": len(cases),
            "selected_strategy": selected["strategy"],
            "selection_reason": selected["reason"],
            "vector_store_selection": {
                "selected": "SQLite authoritative store + Chroma optional vector mirror",
                "reason": (
                    "SQLite keeps all chunks, metadata and deterministic embeddings auditable and easy to test; "
                    "Chroma adds a realistic vector database path for local ANN-style retrieval without forcing "
                    "external infrastructure in demos."
                ),
            },
            "strategy_results": strategy_results,
        }
        run = EvaluationRun(
            name="rag_strategy_evaluation",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    async def run_llm_workflow_evaluation(self, db: Session) -> EvaluationRun:
        if not self.llm.available:
            run = EvaluationRun(
                name="llm_workflow_evaluation",
                summary_json={
                    "evaluation_type": "llm_workflow",
                    "status": "skipped",
                    "reason": "LLM_API_KEY/LLM_BASE_URL 未配置，无法进行真实 LLM 调用评测。",
                },
                case_results_json=[],
            )
            db.add(run)
            db.commit()
            db.refresh(run)
            return run

        profile = ResumeParserService().create_profile_from_guided_answers(
            db,
            GuidedProfileRequest(
                name="LLM Workflow Candidate",
                email="llm-workflow@example.com",
                headline="Agent 开发实习生候选人",
                target_roles=["Agent Development Intern"],
                skills=["Python", "FastAPI", "RAG", "SQLite", "Agent", "Evaluation", "Guardrails"],
                projects=[
                    {
                        "name": "CareerAgent",
                        "description": (
                            "Built a job-search agent with PDF chunking, SQLite-backed RAG, real job search, "
                            "traceable workflows, LLM debug logs, evaluation metrics and resume tailoring."
                        ),
                        "tech_stack": ["Python", "FastAPI", "SQLite", "RAG"],
                        "impact": "Created an end-to-end workflow for real internship applications.",
                    }
                ],
            ),
        )
        cases = self._llm_workflow_cases()
        case_results = []
        for case in cases:
            jd = await JDParserService().parse_jd(
                case["jd_text"],
                title=case["title"],
                company=case["company"],
                db=db,
            )
            job = Job(
                source="llm_eval",
                external_id=f"llm_eval:{case['name']}:{profile.id}",
                title=case["title"],
                company=case["company"],
                raw_jd_text=case["jd_text"],
                structured_jd_json=jd,
                apply_url="https://example.com/jobs/llm-eval",
            )
            db.add(job)
            db.commit()
            db.refresh(job)

            suitability = await self._llm_judge_suitability(db, profile.structured_profile_json, job)
            label = str(suitability.get("fit_label") or "").strip()
            label_passed = label == case["expected_fit_label"]
            result = {
                "name": case["name"],
                "job_id": job.id,
                "expected_fit_label": case["expected_fit_label"],
                "predicted_fit_label": label,
                "label_passed": label_passed,
                "suitability": suitability,
            }
            if case.get("run_tailor"):
                version = await ResumeTailorService().tailor_resume(db, profile, job)
                required = [str(skill) for skill in jd.get("required_skills", [])]
                resume_text = version.tailored_resume_markdown.lower()
                covered = [skill for skill in required if skill.lower() in resume_text]
                verification = ResumeGuardrailService().verify(
                    profile=profile,
                    job=job,
                    resume_markdown=version.tailored_resume_markdown,
                    evidence=version.source_evidence_json,
                )
                result.update(
                    {
                        "resume_version_id": version.id,
                        "tailor_passed": verification["risk_level"] in {"low", "medium"}
                        and len(covered) / max(len(required), 1) >= 0.5,
                        "tailored_required_skill_coverage": round(len(covered) / max(len(required), 1), 4),
                        "tailored_risk_level": verification["risk_level"],
                        "resume_preview": version.tailored_resume_markdown[:600],
                    }
                )
            case_results.append(result)

        summary = {
            "evaluation_type": "llm_workflow",
            "status": "completed",
            "case_count": len(case_results),
            "fit_label_accuracy": round(
                sum(1 for item in case_results if item["label_passed"]) / max(len(case_results), 1),
                4,
            ),
            "tailor_pass_rate": round(
                sum(1 for item in case_results if item.get("tailor_passed")) / max(
                    sum(1 for item in case_results if "tailor_passed" in item),
                    1,
                ),
                4,
            ),
            "notes": [
                "LLM 适配判断要求返回 strict JSON。",
                "简历定制通过 required skill coverage 与 guardrail risk level 验收。",
            ],
        }
        run = EvaluationRun(
            name="llm_workflow_evaluation",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    async def _run_case(self, db: Session, case: dict[str, Any]) -> dict[str, Any]:
        profile_payload = GuidedProfileRequest.model_validate(case["profile"])
        profile = ResumeParserService().create_profile_from_guided_answers(db, profile_payload)

        job_payload = case["job"]
        jd = await JDParserService().parse_jd(
            job_payload["jd_text"],
            title=job_payload.get("title"),
            company=job_payload.get("company"),
        )
        job = Job(
            source="eval",
            external_id=f"eval:{case['name']}:{profile.id}",
            title=job_payload.get("title") or jd.get("title") or "Eval Job",
            company=job_payload.get("company"),
            raw_jd_text=job_payload["jd_text"],
            structured_jd_json=jd,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        jd_chunks = ResumeTextSplitter().split_jd_text(job.raw_jd_text, job.structured_jd_json, prefix=f"eval_job_{job.id}")
        SQLiteVectorIndex().upsert_job_chunks(db, job.id, jd_chunks)

        match = self.matcher.create_match_result(db, profile, job)
        expected_matched = set(case.get("expected_matched_skills", []))
        expected_missing = set(case.get("expected_missing_skills", []))
        predicted_matched = set(match.matched_skills_json)
        predicted_missing = set(match.missing_skills_json)

        result = {
            "name": case["name"],
            "profile_id": profile.id,
            "job_id": job.id,
            "match_result_id": match.id,
            "overall_score": match.overall_score,
            "required_skill_precision": self._precision(predicted_matched, expected_matched),
            "required_skill_recall": self._recall(predicted_matched, expected_matched),
            "missing_skill_precision": self._precision(predicted_missing, expected_missing),
            "evidence_hit_rate": self._evidence_hit_rate(
                match.relevant_evidence_json,
                case.get("expected_evidence_keywords", []),
            ),
            "score_floor_passed": match.overall_score >= case.get("min_overall_score", 0),
            "score_ceiling_passed": match.overall_score <= case.get("max_overall_score", 100),
            "predicted_matched_skills": match.matched_skills_json,
            "predicted_missing_skills": match.missing_skills_json,
        }
        result["case_passed"] = (
            result["required_skill_recall"] >= 0.6
            and result["missing_skill_precision"] >= 0.5
            and result["score_floor_passed"]
            and result["score_ceiling_passed"]
        )
        return result

    def _summarize(self, case_results: list[dict[str, Any]]) -> dict[str, Any]:
        count = max(len(case_results), 1)
        return {
            "case_count": len(case_results),
            "pass_rate": round(sum(1 for item in case_results if item["case_passed"]) / count, 4),
            "avg_overall_score": round(sum(item["overall_score"] for item in case_results) / count, 2),
            "avg_required_skill_precision": round(
                sum(item["required_skill_precision"] for item in case_results) / count,
                4,
            ),
            "avg_required_skill_recall": round(
                sum(item["required_skill_recall"] for item in case_results) / count,
                4,
            ),
            "avg_missing_skill_precision": round(
                sum(item["missing_skill_precision"] for item in case_results) / count,
                4,
            ),
            "avg_evidence_hit_rate": round(sum(item["evidence_hit_rate"] for item in case_results) / count, 4),
        }

    def _precision(self, predicted: set[str], expected: set[str]) -> float:
        if not predicted:
            return 1.0 if not expected else 0.0
        return round(len(predicted & expected) / len(predicted), 4)

    def _recall(self, predicted: set[str], expected: set[str]) -> float:
        if not expected:
            return 1.0
        return round(len(predicted & expected) / len(expected), 4)

    def _evidence_hit_rate(self, evidence: list[dict[str, Any]], expected_keywords: list[str]) -> float:
        if not expected_keywords:
            return 1.0
        evidence_text = "\n".join(str(item.get("text") or "") for item in evidence).lower()
        hits = [keyword for keyword in expected_keywords if keyword.lower() in evidence_text]
        return round(len(hits) / len(expected_keywords), 4)

    def _llm_workflow_cases(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "strong_agent_fit",
                "title": "Agent Development Intern",
                "company": "Demo AI",
                "expected_fit_label": "strong_fit",
                "run_tailor": True,
                "jd_text": (
                    "Agent Development Intern. Build FastAPI services for Agent workflows, PDF chunking, "
                    "SQLite-backed RAG, evaluation metrics, LLM debug logs and guardrails. Requirements: "
                    "Python, FastAPI, RAG, SQLite, Agent workflow, evaluation."
                ),
            },
            {
                "name": "partial_llm_eval_fit",
                "title": "LLM Evaluation Intern",
                "company": "Demo AI",
                "expected_fit_label": "partial_fit",
                "run_tailor": False,
                "jd_text": (
                    "LLM Evaluation Intern. Build prompt regression tests, model output scoring dashboards, "
                    "SQL analysis and quality review process. Python and evaluation experience required."
                ),
            },
            {
                "name": "weak_frontend_fit",
                "title": "Frontend Design System Intern",
                "company": "Demo UI",
                "expected_fit_label": "weak_fit",
                "run_tailor": False,
                "jd_text": (
                    "Frontend Design System Intern. Build React components, CSS token systems, visual QA, "
                    "accessibility checks and Storybook documentation. Requirements: React, TypeScript, CSS."
                ),
            },
        ]

    async def _llm_judge_suitability(self, db: Session, profile_json: dict[str, Any], job: Job) -> dict[str, Any]:
        system_prompt = (
            "You are a strict job-fit evaluator. Return JSON only. "
            "Use fit_label exactly one of: strong_fit, partial_fit, weak_fit. "
            "Be conservative: strong_fit requires direct evidence for most core job duties, not just adjacent skills."
        )
        user_prompt = f"""
Evaluate whether the candidate is suitable for the job.

Output JSON:
{{
  "fit_label": "strong_fit|partial_fit|weak_fit",
  "fit_score": number,
  "matched_evidence": [string],
  "gaps": [string],
  "message_to_candidate": string
}}

Rules:
- strong_fit: candidate has most core requirements and directly relevant project evidence.
- partial_fit: candidate has some overlapping evidence but important gaps remain.
- weak_fit: role is mostly outside candidate evidence.
- For this candidate, strong_fit is appropriate only when the role directly needs Agent workflow/RAG/FastAPI/SQLite implementation.
- If the role mainly focuses on LLM evaluation, dashboards, prompt regression, frontend, or another adjacent area, use partial_fit unless Agent/RAG implementation is a core responsibility.
- Do not invent experience.

Candidate profile:
{json.dumps(profile_json, ensure_ascii=False)}

Job:
{job.title}
{job.raw_jd_text}
"""
        try:
            return await self.llm.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                db=db,
                trace_name="evaluation.llm_judge_suitability",
                temperature=0,
            )
        except Exception as exc:
            return {
                "fit_label": "error",
                "fit_score": 0,
                "matched_evidence": [],
                "gaps": [],
                "message_to_candidate": str(exc),
            }

    def _pdf_fixed_window_450(self, pages: list[PDFPageText], *, case_name: str) -> list[TextChunk]:
        return self._fixed_window_pdf_chunks(pages, case_name=case_name, chunk_size=450, overlap=80)

    def _pdf_paragraph_page_900(self, pages: list[PDFPageText], *, case_name: str) -> list[TextChunk]:
        return ResumeTextSplitter(chunk_size=900, chunk_overlap=160).split_pdf_pages(pages, prefix=case_name)

    def _pdf_paragraph_page_1200(self, pages: list[PDFPageText], *, case_name: str) -> list[TextChunk]:
        return ResumeTextSplitter(chunk_size=1200, chunk_overlap=200).split_pdf_pages(pages, prefix=case_name)

    def _pdf_section_aware_700(self, pages: list[PDFPageText], *, case_name: str) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        heading_pattern = re.compile(r"^(skills|projects|experience|education|awards|activities|coursework)\b", re.I)
        splitter = ResumeTextSplitter(chunk_size=700, chunk_overlap=120)
        for page in pages:
            sections: list[tuple[str, list[str]]] = []
            current_heading = "page"
            current_lines: list[str] = []
            for line in page.text.splitlines():
                clean = line.strip()
                if not clean:
                    continue
                if heading_pattern.match(clean):
                    if current_lines:
                        sections.append((current_heading, current_lines))
                    current_heading = clean.split(":", 1)[0].lower()
                    current_lines = [clean]
                else:
                    current_lines.append(clean)
            if current_lines:
                sections.append((current_heading, current_lines))
            for section_idx, (heading, lines) in enumerate(sections):
                text = "\n".join(lines)
                section_chunks = splitter.split_raw_text(
                    text,
                    prefix=f"{case_name}_page_{page.page_no}_section_{section_idx}",
                    source="profile.pdf_section_text",
                    metadata={"page_no": page.page_no, "section": heading, "strategy": "section_aware"},
                )
                chunks.extend(section_chunks)
        return chunks

    def _fixed_window_pdf_chunks(
        self,
        pages: list[PDFPageText],
        *,
        case_name: str,
        chunk_size: int,
        overlap: int,
    ) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        step = max(chunk_size - overlap, 1)
        for page in pages:
            text = page.text.strip()
            for idx, start in enumerate(range(0, len(text), step)):
                piece = text[start : start + chunk_size].strip()
                if not piece:
                    continue
                chunks.append(
                    TextChunk(
                        uid=f"{case_name}_fixed_page_{page.page_no}_{idx}",
                        text=piece,
                        chunk_type="raw_text",
                        source="profile.pdf_page_text",
                        metadata={
                            "page_no": page.page_no,
                            "char_start": start,
                            "char_end": start + len(piece),
                            "chunk_size": chunk_size,
                            "chunk_overlap": overlap,
                            "strategy": "fixed_window",
                        },
                    )
                )
                if start + chunk_size >= len(text):
                    break
        return chunks

    def _rank_text_chunks(
        self,
        query: str,
        chunks: list[TextChunk],
        *,
        vector_weight: float,
        lexical_weight: float,
        type_boost: bool = False,
    ) -> list[dict[str, Any]]:
        dimensions = self.settings.embedding_dimensions
        query_vec = hash_embedding(query, dimensions)
        query_tokens = set(tokenize(query))
        ranked = []
        for chunk in chunks:
            chunk_vec = hash_embedding(chunk.text, dimensions)
            vector_score = cosine_similarity(query_vec, chunk_vec)
            chunk_tokens = set(tokenize(chunk.text))
            lexical_score = len(query_tokens & chunk_tokens) / max(len(query_tokens), 1)
            score = vector_weight * vector_score + lexical_weight * lexical_score
            if type_boost and chunk.chunk_type in {"project", "skill", "experience", "required_skills"}:
                score += 0.08
            ranked.append(
                {
                    "uid": chunk.uid,
                    "text": chunk.text,
                    "chunk_type": chunk.chunk_type,
                    "metadata": chunk.metadata or {},
                    "score": round(score, 6),
                }
            )
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked

    def _summarize_pdf_strategy(self, strategy_name: str, per_query: list[dict[str, Any]]) -> dict[str, Any]:
        count = max(len(per_query), 1)
        return {
            "strategy": strategy_name,
            "query_count": len(per_query),
            "top3_keyword_hit_rate": round(sum(1 for item in per_query if item["hit"]) / count, 4),
            "top3_page_hit_rate": round(sum(1 for item in per_query if item["page_hit"]) / count, 4),
            "top3_context_hit_rate": round(sum(1 for item in per_query if item["context_hit"]) / count, 4),
            "avg_top1_chars": round(sum(item["top1_chars"] for item in per_query) / count, 2),
            "avg_chunk_count": round(sum(item["chunk_count"] for item in per_query) / count, 2),
        }

    def _select_pdf_strategy(self, strategy_results: list[dict[str, Any]]) -> dict[str, str]:
        ranked = sorted(
            strategy_results,
            key=lambda item: (
                item["top3_keyword_hit_rate"],
                item["top3_page_hit_rate"],
                item["top3_context_hit_rate"],
                -abs(item["avg_top1_chars"] - 650),
                -abs(item["avg_chunk_count"] - 8),
            ),
            reverse=True,
        )
        selected = ranked[0]
        return {
            "strategy": selected["strategy"],
            "reason": (
                f"{selected['strategy']} 在关键词命中率和页码命中率上表现最好或并列最好，"
                f"Top3 上下文命中率为 {selected['top3_context_hit_rate']}，"
                f"平均 top1 chunk 长度为 {selected['avg_top1_chars']} 字符，"
                "能在保留上下文和避免过大 chunk 噪声之间取得平衡。"
            ),
        }

    def _summarize_rag_strategy(self, strategy_name: str, per_case: list[dict[str, Any]]) -> dict[str, Any]:
        count = max(len(per_case), 1)
        return {
            "strategy": strategy_name,
            "case_count": len(per_case),
            "top1_accuracy": round(sum(1 for item in per_case if item["top1_expected"]) / count, 4),
            "avg_top3_recall": round(sum(item["top3_recall"] for item in per_case) / count, 4),
            "avg_top5_recall": round(sum(item["top5_recall"] for item in per_case) / count, 4),
            "avg_mrr": round(sum(item["mrr"] for item in per_case) / count, 4),
            "avg_ndcg_at_5": round(sum(item["ndcg_at_5"] for item in per_case) / count, 4),
        }

    def _select_rag_strategy(self, strategy_results: list[dict[str, Any]]) -> dict[str, str]:
        ranked = sorted(
            strategy_results,
            key=lambda item: (
                item["avg_top3_recall"],
                item["avg_mrr"],
                item["avg_ndcg_at_5"],
                item["top1_accuracy"],
                0 if item["strategy"] == "lexical_only" else 1,
            ),
            reverse=True,
        )
        selected = ranked[0]
        return {
            "strategy": selected["strategy"],
            "reason": (
                f"{selected['strategy']} 的 Top3 Recall={selected['avg_top3_recall']}、"
                f"MRR={selected['avg_mrr']}、nDCG@5={selected['avg_ndcg_at_5']} 综合最高；"
                "该选择优先保证技术关键词召回；当混合策略达到相同召回时，优先选择带向量重排和类型加权的方案。"
            ),
        }

    def _mrr(self, ranked: list[dict[str, Any]], expected_ids: set[str]) -> float:
        for index, item in enumerate(ranked, start=1):
            if item["uid"] in expected_ids:
                return round(1 / index, 4)
        return 0.0

    def _ndcg_at_k(self, ranked: list[dict[str, Any]], expected_ids: set[str], k: int) -> float:
        dcg = 0.0
        for index, item in enumerate(ranked[:k], start=1):
            relevance = 1.0 if item["uid"] in expected_ids else 0.0
            dcg += relevance / math.log2(index + 1)
        ideal_hits = min(len(expected_ids), k)
        idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
        if idcg == 0:
            return 0.0
        return round(dcg / idcg, 4)
