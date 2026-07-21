import asyncio
import json
import math
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.agents.orchestrator import AgentOrchestrator
from app.core.config import get_settings
from app.models.entities import AgentArtifact, AgentStep, EvaluationRun, Job, JobChunk, Profile
from app.models.schemas import AgentRunRequest, GuidedProfileRequest
from app.services.context_compressor import ContextCompressor
from app.services.application_guardrails import ApplicationPacketGuardrail
from app.services.guardrails import ResumeGuardrailService
from app.services.interview_delivery import InterviewPrepDeliveryService
from app.services.interview_evaluation_fixture import DeterministicInterviewEvaluationLLM
from app.services.interview_sources import InterviewExperienceSearchResult, InterviewExperienceSourceRegistry
from app.services.interview_prep import InterviewPrepService
from app.services.jd_parser import JDParserService
from app.services.job_relevance import (
    is_agent_related_posting,
    is_internship_like_posting,
    is_query_relevant_posting,
    rank_postings_for_query,
    score_job_posting,
    source_posting_haystack,
)
from app.services.job_search import JobSearchService
from app.services.prompt_injection_guard import PromptInjectionGuard
from app.services.job_sources import JobPosting, JobSourceRegistry
from app.services.resume_tailor import ResumeTailorService
from app.core.llm import LLMClient, LLMConfigurationError, format_exception, llm_trace_context
from app.services.embedding_service import EmbeddingService
from app.services.matcher import MatcherService
from app.services.reranker import RerankerService
from app.services.resume_parser import ResumeParserService
from app.services.text_splitter import PDFPageText, ResumeTextSplitter, TextChunk
from app.services.vector_index import SQLiteVectorIndex, cosine_similarity, expand_query_text, tokenize


REAL_JD_INGEST_PROBE_PATTERNS: dict[str, list[str]] = {
    "Agent": [r"\bagents?\b", "智能体"],
    "RAG": [r"\brag\b", r"\bretrieval[- ]augmented generation\b", "检索增强", "知识库"],
    "LLM": [r"\bllms?\b", r"\blarge language models?\b", "大语言模型", "大模型"],
    "Python": [r"\bpython\b"],
    "FastAPI": [r"\bfastapi\b"],
    "SQL": [r"\bsql\b"],
    "SQLite": [r"\bsqlite\b"],
    "Vector Database": [r"\bvector (database|store|index|search)\b", "向量数据库", "向量检索"],
    "Embedding": [r"\bembeddings?\b", "嵌入模型", "语义向量"],
    "Reranker": [r"\brerank(er|ing)?\b", r"\bcross[- ]encoder\b", "重排序"],
    "Tool Calling": [r"\btool calling\b", r"\bfunction calling\b", "工具调用"],
    "Workflow": [r"\bworkflows?\b", r"\borchestration\b", "工作流", "编排"],
    "Evaluation": [r"\beval(uation)?\b", "评测", "评估"],
    "Guardrail": [r"\bguardrails?\b", "安全护栏", "风控策略"],
    "Prompt Engineering": [r"\bprompt engineering\b", r"\bprompts?\b", "提示词工程"],
    "Prompt Injection": [r"\bprompt injection\b", "提示词注入"],
    "A/B Testing": [r"\ba/b tests?\b", r"\ba/b testing\b", r"\bab tests?\b", "A/B实验", "AB实验"],
    "Feature Store": [r"\bfeature stores?\b", "特征平台", "特征库"],
    "MLflow": [r"\bmlflow\b"],
    "Airflow": [r"\bairflow\b"],
    "Spark": [r"\bspark\b"],
    "Kafka": [r"\bkafka\b"],
    "Recommendation": [r"\brecommendation(s)?\b", "推荐系统", "推荐算法"],
    "Ranking": [r"\branking\b", "排序模型", "召回排序"],
    "CTR": [r"\bctr\b", "点击率"],
    "Docker": [r"\bdocker\b"],
    "Kubernetes": [r"\bkubernetes\b", r"\bk8s\b"],
}


class EvaluationJobSearchService:
    def __init__(self, postings: list[dict[str, Any]], *, namespace: str) -> None:
        self.postings = postings
        self.namespace = namespace
        self.jd_parser = JDParserService()
        self.splitter = ResumeTextSplitter()
        self.vector_index = SQLiteVectorIndex()

    async def search(
        self,
        db: Session,
        *,
        query: str,
        location: str | None = None,
        internship_only: bool = True,
        limit: int = 20,
        store_results: bool = True,
    ) -> tuple[list[Job], dict[str, str]]:
        jobs = []
        for index, item in enumerate(self.postings[:limit]):
            raw_external_id = str(item.get("external_id") or f"eval_job_{index}")
            posting = JobPosting(
                source="eval_agent_full_flow",
                external_id=f"{self.namespace}:{raw_external_id}",
                title=str(item.get("title") or "Evaluation Job"),
                company=item.get("company"),
                location=item.get("location") or location,
                job_type=item.get("job_type") or "internship",
                apply_url=item.get("apply_url") or f"https://example.com/jobs/{index}",
                raw_jd_text=str(item.get("jd_text") or ""),
                payload={**item, "eval_external_id": raw_external_id},
            )
            structured = item.get("structured_jd") or await self.jd_parser.parse_jd(
                posting.raw_jd_text,
                title=posting.title,
                company=posting.company,
                location=posting.location,
            )
            job = Job(
                source=posting.source,
                external_id=posting.external_id,
                title=posting.title,
                company=posting.company,
                location=posting.location,
                job_type=posting.job_type,
                apply_url=posting.apply_url,
                raw_jd_text=posting.raw_jd_text,
                structured_jd_json=structured,
                source_payload_json=posting.payload,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            chunks = self.splitter.split_jd_text(job.raw_jd_text, job.structured_jd_json or {}, prefix=f"eval_job_{job.id}")
            self.vector_index.upsert_job_chunks(db, job.id, chunks)
            jobs.append(job)
        return jobs, {}


class EvaluationService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.matcher = MatcherService()
        self.llm = LLMClient()
        self.embedding_service = EmbeddingService(settings=self.settings)
        self.hash_embedding_service = EmbeddingService(settings=self.settings, provider="hash")
        self.reranker = RerankerService(settings=self.settings)
        self.context_compressor = ContextCompressor()
        self.application_guardrail = ApplicationPacketGuardrail()
        interview_llm = self.llm
        self.interview_evaluation_llm_mode = "real_llm"
        if not self.llm.available and self.settings.llm_fallback_enabled:
            interview_llm = DeterministicInterviewEvaluationLLM()
            self.interview_evaluation_llm_mode = "synthetic_eval_fixture"
        self.interview_prep_service = InterviewPrepService(matcher=self.matcher, llm=interview_llm)
        self.interview_delivery = InterviewPrepDeliveryService()
        self.jd_parser = JDParserService()
        self.job_search_service = JobSearchService()
        self.vector_index = SQLiteVectorIndex()
        self.prompt_injection_guard = PromptInjectionGuard()

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

    def run_prompt_injection_evaluation(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "prompt_injection_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        case_results = []
        for case in cases:
            result = self.prompt_injection_guard.detect(case["text"], source=case.get("source") or "unknown")
            sanitized, _ = self.prompt_injection_guard.sanitize_for_llm(
                case["text"], source=case.get("source") or "unknown"
            )
            expected_detected = bool(case.get("expected_detected"))
            expected_categories = set(case.get("expected_categories") or [])
            actual_categories = set(result.categories)
            category_hits = sorted(expected_categories & actual_categories)
            sanitized_absent = [
                token
                for token in case.get("expected_sanitized_absent", [])
                if str(token).lower() not in sanitized.lower()
            ]
            passed = result.detected == expected_detected
            if expected_detected:
                passed = (
                    passed
                    and result.severity == case.get("expected_severity")
                    and expected_categories <= actual_categories
                    and len(sanitized_absent) == len(case.get("expected_sanitized_absent", []))
                )
            case_results.append(
                {
                    "case_name": case.get("name"),
                    "source": case.get("source"),
                    "expected_detected": expected_detected,
                    "actual_detected": result.detected,
                    "expected_severity": case.get("expected_severity"),
                    "actual_severity": result.severity,
                    "expected_categories": sorted(expected_categories),
                    "actual_categories": result.categories,
                    "category_hits": category_hits,
                    "matched_patterns": result.matched_patterns,
                    "sanitized_removed_expected_tokens": sanitized_absent,
                    "passed": passed,
                }
            )
        policy = self._load_prompt_injection_release_policy()
        summary = self._summarize_prompt_injection(case_results, dataset_name=path.name, policy=policy)
        run = EvaluationRun(
            name="prompt_injection_guard_evaluation",
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
                    ranked = self._rank_text_chunks(
                        query["query"],
                        chunks,
                        vector_weight=self.settings.retrieval_vector_weight,
                        lexical_weight=self.settings.retrieval_lexical_weight,
                        type_boost=True,
                        embedding_service=self.embedding_service,
                    )
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
                            "difficulty": query.get("difficulty") or case.get("difficulty") or "unknown",
                            "noise_profile": query.get("noise_profile") or "unknown",
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
            "embedding_model_selection": {
                "configured_provider": self.settings.embedding_provider,
                "configured_model": self.settings.embedding_model_name,
                "fallback": self.settings.embedding_provider_fallback,
                "reason": "PDF chunk 策略评测使用与生产检索相同的 embedding 和检索权重，避免只在离线 hash ranker 上优化。",
            },
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
            "hash_vector_only": {
                "embedding_provider": "hash",
                "vector_weight": 1.0,
                "lexical_weight": 0.0,
                "type_boost": False,
            },
            "hash_lexical_only": {
                "embedding_provider": "hash",
                "vector_weight": 0.0,
                "lexical_weight": 1.0,
                "type_boost": False,
            },
            "hash_lexical_80_vector_15_type_5": {
                "embedding_provider": "hash",
                "vector_weight": 0.15,
                "lexical_weight": 0.8,
                "type_boost": True,
                "query_expansion": True,
            },
            "real_embedding_vector_only": {
                "embedding_provider": "configured",
                "vector_weight": 1.0,
                "lexical_weight": 0.0,
                "type_boost": False,
                "query_expansion": True,
            },
            "real_embedding_70_vector_30_lexical": {
                "embedding_provider": "configured",
                "vector_weight": 0.7,
                "lexical_weight": 0.3,
                "type_boost": False,
                "query_expansion": True,
            },
            "real_embedding_55_vector_40_lexical_5_type": {
                "embedding_provider": "configured",
                "vector_weight": 0.55,
                "lexical_weight": 0.4,
                "type_boost": True,
                "query_expansion": True,
            },
            "real_embedding_45_vector_50_lexical_5_type": {
                "embedding_provider": "configured",
                "vector_weight": 0.45,
                "lexical_weight": 0.5,
                "type_boost": True,
                "query_expansion": True,
            },
            "real_embedding_top20_rerank": {
                "embedding_provider": "configured",
                "vector_weight": 0.45,
                "lexical_weight": 0.5,
                "type_boost": True,
                "query_expansion": True,
                "reranker": True,
                "rerank_top_n": 20,
            },
        }
        strategy_results = []
        case_results = []
        for strategy_name, config in strategies.items():
            per_case = []
            embedding_service = self._embedding_service_for_strategy(config)
            reranker = self.reranker if config.get("reranker") else None
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
                    embedding_service=embedding_service,
                    reranker=reranker,
                    rerank_top_n=int(config.get("rerank_top_n", self.settings.reranker_top_n)),
                )
                expected_ids = set(case["expected_chunk_ids"])
                top3 = ranked[:3]
                top5 = ranked[:5]
                embedding_info = ranked[0].get("embedding", {}) if ranked else {}
                rerank_info = (ranked[0].get("metadata", {}).get("rerank") or {}) if ranked else {}
                per_case.append(
                    {
                        "case": case["name"],
                        "difficulty": case.get("difficulty", "unknown"),
                        "noise_profiles": case.get("noise_profiles", []),
                        "top3_recall": self._recall({item["uid"] for item in top3}, expected_ids),
                        "top5_recall": self._recall({item["uid"] for item in top5}, expected_ids),
                        "mrr": self._mrr(ranked, expected_ids),
                        "ndcg_at_5": self._ndcg_at_k(ranked, expected_ids, 5),
                        "top1_expected": ranked[0]["uid"] in expected_ids if ranked else False,
                        "top3_ids": [item["uid"] for item in top3],
                        "embedding_provider": embedding_info.get("provider"),
                        "embedding_model": embedding_info.get("model"),
                        "embedding_fallback_reason": embedding_info.get("fallback_reason"),
                        "reranker_provider": rerank_info.get("reranker_provider"),
                        "reranker_model": rerank_info.get("reranker_model"),
                        "reranker_fallback_reason": rerank_info.get("fallback_reason"),
                    }
                )
            summary = self._summarize_rag_strategy(strategy_name, per_case)
            summary.update(
                {
                    "configured_embedding_provider": config["embedding_provider"],
                    "uses_reranker": bool(config.get("reranker")),
                    "vector_weight": config["vector_weight"],
                    "lexical_weight": config["lexical_weight"],
                    "type_boost": bool(config["type_boost"]),
                }
            )
            strategy_results.append(summary)
            case_results.extend({"strategy": strategy_name, **item} for item in per_case)

        selected = self._select_rag_strategy(strategy_results)
        summary = {
            "evaluation_type": "rag_strategy",
            "dataset": str(path.name),
            "case_count": len(cases),
            "selected_strategy": selected["strategy"],
            "selection_reason": selected["reason"],
            "embedding_model_selection": {
                "configured_provider": self.settings.embedding_provider,
                "configured_model": self.settings.embedding_model_name,
                "fallback": self.settings.embedding_provider_fallback,
                "reason": (
                    "默认使用多语言 SentenceTransformer 作为本地真实 embedding 模型，能覆盖中文简历、"
                    "英文 JD 和中英混合技术词；默认不做静默降级，模型不可用时直接报错并由 trace 记录。"
                ),
            },
            "reranker_selection": {
                "configured_provider": self.settings.reranker_provider,
                "configured_model": self.settings.reranker_model_name,
                "top_n": self.settings.reranker_top_n,
                "reason": (
                    "一阶段检索保召回，二阶段 reranker 只处理 Top20，成本可控，适合简历证据和 JD 证据这种"
                    "候选集较小但排序质量要求高的场景。"
                ),
            },
            "vector_store_selection": {
                "selected": "SQLite authoritative store + Chroma optional vector mirror",
                "reason": (
                    "SQLite 保存 Profile、JD、chunk、metadata、embedding 和评测结果，是可审计的权威存储；"
                    "Chroma 作为本地持久化向量库镜像，体现真实 RAG 工程的向量检索组件，但不会把职位 JD "
                    "和简历证据的业务元数据锁死在向量库里。"
                ),
                "alternatives_considered": ["FAISS", "Qdrant", "Milvus", "pgvector"],
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

    async def run_agent_full_flow_evaluation(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "agent_full_flow_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        run_namespace = uuid.uuid4().hex[:12]
        case_results = [
            await self._run_agent_full_flow_case(db, case, namespace=f"{run_namespace}:{index}:{case['name']}")
            for index, case in enumerate(cases)
        ]
        summary = self._summarize_agent_full_flow(case_results, path)
        run = EvaluationRun(
            name="agent_full_flow_evaluation",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    async def run_jd_parser_evaluation(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "jd_parser_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        case_results = [await self._run_jd_parser_case(db, case) for case in cases]
        summary = self._summarize_jd_parser(case_results, path)
        run = EvaluationRun(
            name="jd_parser_evaluation",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def run_job_relevance_evaluation(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "job_relevance_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        case_results = [self._run_job_relevance_case(case) for case in cases]
        summary = self._summarize_job_relevance(case_results, path)
        run = EvaluationRun(
            name="job_relevance_evaluation",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def run_application_packet_evaluation(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "application_packet_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        case_results = [self._run_application_packet_case(case) for case in cases]
        summary = self._summarize_application_packet(case_results, path)
        run = EvaluationRun(
            name="application_packet_evaluation",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    def run_interview_prep_evaluation(self, db: Session, *, dataset_path: Path | None = None) -> EvaluationRun:
        path = dataset_path or self.settings.base_path / "evals" / "interview_prep_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))
        namespace = f"interview_eval:{uuid.uuid4().hex[:8]}"
        case_results = [
            self._run_interview_prep_case(db, case, namespace=f"{namespace}:{index}")
            for index, case in enumerate(cases)
        ]
        summary = self._summarize_interview_prep(case_results, path)
        run = EvaluationRun(
            name="interview_prep_evaluation",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    async def run_real_job_source_smoke(
        self,
        db: Session,
        *,
        query: str = "Agent 开发实习生",
        location: str | None = None,
        limit: int = 8,
        sources: list[str] | None = None,
        source_registry: JobSourceRegistry | None = None,
    ) -> EvaluationRun:
        registry = source_registry or JobSourceRegistry()
        selected_sources = registry.select(sources)
        started = time.perf_counter()

        async def _probe(source: Any) -> dict[str, Any]:
            source_started = time.perf_counter()
            try:
                postings = await source.search(query=query, location=location, limit=limit)
                ranked_postings = rank_postings_for_query(postings, query)
                relevance_scores = [score_job_posting(posting, query).score for posting in ranked_postings]
                sample_jobs = [
                    self._summarize_source_posting(posting, query=query) for posting in ranked_postings[:limit]
                ]
                return {
                    "source": source.name,
                    "status": "completed",
                    "source_reachable": True,
                    "has_results": bool(ranked_postings),
                    "result_count": len(ranked_postings),
                    "internship_like_count": sum(
                        1 for posting in ranked_postings if self._is_internship_like_posting(posting)
                    ),
                    "query_relevant_count": sum(
                        1 for posting in ranked_postings if self._is_query_relevant_posting(posting, query)
                    ),
                    "agent_related_count": sum(1 for posting in ranked_postings if self._is_agent_related_posting(posting)),
                    "non_empty_jd_count": sum(1 for posting in ranked_postings if bool(posting.raw_jd_text.strip())),
                    "apply_url_count": sum(1 for posting in ranked_postings if bool(posting.apply_url)),
                    "relevance_score_sum": round(sum(relevance_scores), 4),
                    "relevance_score_count": len(relevance_scores),
                    "top_relevance_score": relevance_scores[0] if relevance_scores else 0.0,
                    "latency_ms": int((time.perf_counter() - source_started) * 1000),
                    "error": None,
                    "sample_jobs": sample_jobs,
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "source": source.name,
                    "status": "source_error",
                    "source_reachable": False,
                    "has_results": False,
                    "result_count": 0,
                    "internship_like_count": 0,
                    "query_relevant_count": 0,
                    "agent_related_count": 0,
                    "non_empty_jd_count": 0,
                    "apply_url_count": 0,
                    "relevance_score_sum": 0.0,
                    "relevance_score_count": 0,
                    "top_relevance_score": 0.0,
                    "latency_ms": int((time.perf_counter() - source_started) * 1000),
                    "error": format_exception(exc),
                    "sample_jobs": [],
                }

        case_results = await asyncio.gather(*[_probe(source) for source in selected_sources])
        summary = self._summarize_real_job_source_smoke(
            case_results,
            query=query,
            location=location,
            limit=limit,
            source_names=[source.name for source in selected_sources],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        run = EvaluationRun(
            name="real_job_source_smoke",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    async def run_interview_source_smoke(
        self,
        db: Session,
        *,
        query: str = "Agent 开发实习生 面经",
        limit: int = 5,
        sources: list[str] | None = None,
        source_registry: InterviewExperienceSourceRegistry | None = None,
    ) -> EvaluationRun:
        registry = source_registry or InterviewExperienceSourceRegistry()
        selected_sources = registry.select(sources)
        started = time.perf_counter()

        async def _probe(source: Any) -> dict[str, Any]:
            source_started = time.perf_counter()
            try:
                rows = await source.search(query=query, limit=limit)
                sample_experiences = [self._summarize_interview_source_result(row, query=query) for row in rows[:limit]]
                return {
                    "source": source.name,
                    "status": "completed",
                    "source_reachable": True,
                    "has_results": bool(rows),
                    "result_count": len(rows),
                    "url_count": sum(1 for row in rows if row.url),
                    "interview_signal_count": sum(1 for row in rows if self._has_interview_signal(row)),
                    "query_relevant_count": sum(1 for row in rows if self._is_interview_query_relevant(row, query)),
                    "content_extractable_count": sum(1 for row in rows if self._has_extractable_interview_content(row)),
                    "latency_ms": int((time.perf_counter() - source_started) * 1000),
                    "error": None,
                    "sample_experiences": sample_experiences,
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "source": source.name,
                    "status": "source_error",
                    "source_reachable": False,
                    "has_results": False,
                    "result_count": 0,
                    "url_count": 0,
                    "interview_signal_count": 0,
                    "query_relevant_count": 0,
                    "content_extractable_count": 0,
                    "latency_ms": int((time.perf_counter() - source_started) * 1000),
                    "error": format_exception(exc),
                    "sample_experiences": [],
                }

        case_results = await asyncio.gather(*[_probe(source) for source in selected_sources])
        summary = self._summarize_interview_source_smoke(
            case_results,
            query=query,
            limit=limit,
            source_names=[source.name for source in selected_sources],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        run = EvaluationRun(
            name="interview_source_smoke",
            summary_json=summary,
            case_results_json=case_results,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    async def run_real_job_ingest_smoke(
        self,
        db: Session,
        *,
        query: str = "Agent 开发实习生",
        location: str | None = None,
        limit: int = 3,
        sources: list[str] | None = None,
        source_registry: JobSourceRegistry | None = None,
    ) -> EvaluationRun:
        registry = source_registry or JobSourceRegistry()
        selected_sources = registry.select(sources)
        started = time.perf_counter()
        source_results: list[dict[str, Any]] = []
        job_results: list[dict[str, Any]] = []

        async def _fetch_source(source: Any) -> dict[str, Any]:
            source_started = time.perf_counter()
            try:
                postings = await source.search(query=query, location=location, limit=limit)
                return {
                    "source": source.name,
                    "status": "completed",
                    "source_reachable": True,
                    "result_count": len(postings),
                    "latency_ms": int((time.perf_counter() - source_started) * 1000),
                    "error": None,
                    "postings": postings,
                }
            except Exception as exc:  # noqa: BLE001
                return {
                    "source": source.name,
                    "status": "source_error",
                    "source_reachable": False,
                    "result_count": 0,
                    "latency_ms": int((time.perf_counter() - source_started) * 1000),
                    "error": format_exception(exc),
                    "postings": [],
                }

        fetched_sources = await asyncio.gather(*[_fetch_source(source) for source in selected_sources])
        for source_result in fetched_sources:
            postings = source_result.pop("postings", [])
            source_results.append(source_result)
            for posting in postings[:limit]:
                job_results.append(await self._ingest_smoke_posting(db, posting, query=query))

        summary = self._summarize_real_job_ingest_smoke(
            source_results=source_results,
            job_results=job_results,
            query=query,
            location=location,
            limit=limit,
            source_names=[source.name for source in selected_sources],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        run = EvaluationRun(
            name="real_job_ingest_smoke",
            summary_json=summary,
            case_results_json=[
                {
                    "source_results": source_results,
                    "job_results": job_results,
                }
            ],
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    async def run_llm_workflow_evaluation(
        self,
        db: Session,
        *,
        dataset_path: Path | None = None,
        case_limit: int | None = None,
        case_indexes: list[int] | None = None,
        trace_path: Path | None = None,
        resume_from_last_completed: bool = False,
        progress_callback: Callable[[EvaluationRun], None] | None = None,
    ) -> EvaluationRun:
        if not self.llm.available:
            raise LLMConfigurationError("LLM_API_KEY/LLM_BASE_URL 未配置，无法进行真实 LLM 调用评测。")

        if trace_path is None and resume_from_last_completed:
            trace_path = self.settings.base_path / "data" / "runtime" / "llm_workflow_trace_latest.jsonl"
        path = dataset_path or self.settings.base_path / "evals" / "llm_workflow_cases.json"
        all_cases = json.loads(path.read_text(encoding="utf-8"))
        cases = self._select_llm_cases(all_cases, case_limit=case_limit, case_indexes=case_indexes)
        existing_results = self._load_resumable_llm_results(trace_path, cases) if resume_from_last_completed else []
        remaining_cases = cases[len(existing_results) :]
        run = EvaluationRun(
            name="llm_workflow_evaluation",
            summary_json={
                "evaluation_type": "llm_workflow",
                "status": "running",
                "dataset": path.name,
                "case_count": len(cases),
                "completed_cases": len(existing_results),
                "remaining_cases": len(remaining_cases),
                "trace_path": str(trace_path) if trace_path else None,
                "resume_from_last_completed": resume_from_last_completed,
                "resumed_case_count": len(existing_results),
            },
            case_results_json=list(existing_results),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        if progress_callback:
            progress_callback(run)
        case_results: list[dict[str, Any]] = list(existing_results)
        if trace_path:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            if not resume_from_last_completed:
                trace_path.write_text("", encoding="utf-8")
            elif not trace_path.exists():
                trace_path.write_text("", encoding="utf-8")
        if not remaining_cases:
            summary = self._summarize_llm_workflow(case_results, path)
            summary.update(
                {
                    "completed_cases": len(case_results),
                    "remaining_cases": 0,
                    "trace_path": str(trace_path) if trace_path else None,
                    "resume_from_last_completed": resume_from_last_completed,
                    "resumed_case_count": len(existing_results),
                }
            )
            run.summary_json = summary
            run.case_results_json = list(case_results)
            db.add(run)
            db.commit()
            db.refresh(run)
            if progress_callback:
                progress_callback(run)
            return run
        for index, case in enumerate(remaining_cases, start=len(existing_results) + 1):
            case_result = await self._run_llm_workflow_case(db, case, evaluation_run_id=run.id)
            case_results.append(case_result)
            summary = self._summarize_llm_workflow(case_results, path)
            summary.update(
                {
                    "status": "running" if index < len(cases) else summary["status"],
                    "completed_cases": index,
                    "remaining_cases": len(cases) - index,
                    "current_case": case["name"],
                    "trace_path": str(trace_path) if trace_path else None,
                    "resume_from_last_completed": resume_from_last_completed,
                    "resumed_case_count": len(existing_results),
                }
            )
            run.summary_json = summary
            run.case_results_json = list(case_results)
            db.add(run)
            db.commit()
            db.refresh(run)
            if progress_callback:
                progress_callback(run)
            self._append_llm_trace(trace_path, case_result, summary)
        return run

    async def _run_agent_full_flow_case(
        self,
        db: Session,
        case: dict[str, Any],
        *,
        namespace: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": case["name"],
            "difficulty": case.get("difficulty", "unknown"),
            "status": "running",
        }
        try:
            profile = ResumeParserService().create_profile_from_guided_answers(
                db,
                GuidedProfileRequest.model_validate(case["profile"]),
            )
            orchestrator = AgentOrchestrator(
                job_search=EvaluationJobSearchService(case.get("jobs", []), namespace=namespace)
            )
            find_run = await orchestrator.run(
                db,
                self._agent_request(
                    task_type="find_jobs_for_profile",
                    profile_id=profile.id,
                    query=case.get("query") or "Agent 开发实习生",
                    limit=len(case.get("jobs", [])) or 5,
                ),
            )
            find_output = find_run.output_json or {}
            matches = find_output.get("matches", [])
            top_match = matches[0] if matches else {}
            top_job = db.query(Job).filter(Job.id == top_match.get("job_id")).first() if top_match else None
            top_job_eval_external_id = (
                (top_job.source_payload_json or {}).get("eval_external_id") if top_job else None
            )
            expected_top = case.get("expected_top_job_external_id")
            top_job_passed = bool(top_job and top_job_eval_external_id == expected_top)
            min_score = float(case.get("expected_min_top_score", 0))
            max_score = float(case.get("expected_max_top_score", 100))
            top_score = float(top_match.get("overall_score") or 0)
            score_passed = min_score <= top_score <= max_score
            ranking_margin = self._ranking_margin(matches)

            tailor_run = None
            quick_apply_run = None
            tailor_passed = None
            quick_apply_passed = None
            fit_gate_blocked = None
            resume_version_id = None
            application_id = None
            application_packet_passed = None
            if top_job and case.get("run_tailor"):
                tailor_run = await AgentOrchestrator().run(
                    db,
                    self._agent_request(
                        task_type="tailor_resume_for_job",
                        profile_id=profile.id,
                        job_id=top_job.id,
                    ),
                )
                tailor_output = tailor_run.output_json or {}
                resume_version_id = tailor_output.get("resume_version_id")
                version_text = ""
                if resume_version_id:
                    from app.models.entities import ResumeVersion

                    version = db.query(ResumeVersion).filter(ResumeVersion.id == resume_version_id).first()
                    version_text = version.tailored_resume_markdown if version else ""
                keyword_hit = self._keyword_hit_rate(version_text, case.get("expected_resume_keywords", []))
                verification = tailor_output.get("verification") or {}
                tailor_passed = (
                    tailor_run.status == "completed"
                    and bool(verification.get("passed"))
                    and keyword_hit >= float(case.get("min_resume_keyword_hit_rate", 0.6))
                )
                result["resume_keyword_hit_rate"] = keyword_hit

            if top_job and case.get("run_quick_apply"):
                quick_apply_run = await AgentOrchestrator().run(
                    db,
                    self._agent_request(
                        task_type="quick_apply",
                        profile_id=profile.id,
                        job_id=top_job.id,
                        resume_version_id=resume_version_id,
                        application_confirmed=True,
                    ),
                )
                quick_output = quick_apply_run.output_json or {}
                fit_gate_blocked = quick_apply_run.status == "failed" and "Fit gate blocked" in (
                    quick_apply_run.error_message or ""
                )
                expected_block = bool(case.get("expect_quick_apply_blocked"))
                quick_apply_passed = (
                    fit_gate_blocked if expected_block else quick_apply_run.status == "completed"
                )
                application_id = quick_output.get("application_id")
                packet_validation = quick_output.get("packet_validation") or {}
                if not expected_block and quick_apply_run.status == "completed":
                    application_packet_passed = packet_validation.get("passed") is True

            runs = [run for run in [find_run, tailor_run, quick_apply_run] if run is not None]
            trace_passed = all(self._run_has_completed_plan(db, run.id) for run in runs)
            artifact_passed = all(self._run_has_artifact(db, run.id, "execution_plan") for run in runs)
            langgraph_passed = all(self._run_uses_langgraph(run) for run in runs)
            result.update(
                {
                    "status": "completed",
                    "profile_id": profile.id,
                    "find_run_id": find_run.id,
                    "tailor_run_id": tailor_run.id if tailor_run else None,
                    "quick_apply_run_id": quick_apply_run.id if quick_apply_run else None,
                    "top_job_id": top_job.id if top_job else None,
                    "top_job_external_id": top_job.external_id if top_job else None,
                    "top_job_eval_external_id": top_job_eval_external_id,
                    "top_job_score": top_score,
                    "ranking_margin": ranking_margin,
                    "top_job_passed": top_job_passed,
                    "score_passed": score_passed,
                    "tailor_passed": tailor_passed,
                    "quick_apply_passed": quick_apply_passed,
                    "fit_gate_blocked": fit_gate_blocked,
                    "application_packet_passed": application_packet_passed,
                    "trace_passed": trace_passed,
                    "artifact_passed": artifact_passed,
                    "langgraph_passed": langgraph_passed,
                    "resume_version_id": resume_version_id,
                    "application_id": application_id,
                    "matches": matches,
                    "run_trace": [self._agent_run_trace(db, run.id) for run in runs],
                }
            )
            result["case_passed"] = self._agent_full_flow_case_passed(result, case)
            return result
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            result.update(
                {
                    "status": "failed",
                    "error": format_exception(exc),
                    "case_passed": False,
                }
            )
            return result

    def _select_llm_cases(
        self,
        cases: list[dict[str, Any]],
        *,
        case_limit: int | None,
        case_indexes: list[int] | None,
    ) -> list[dict[str, Any]]:
        if case_indexes:
            selected = [cases[index] for index in case_indexes if 0 <= index < len(cases)]
        else:
            selected = list(cases)
        if case_limit is not None:
            selected = selected[: max(case_limit, 0)]
        return selected

    def _load_resumable_llm_results(
        self,
        trace_path: Path | None,
        selected_cases: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if trace_path is None or not trace_path.exists():
            return []
        by_name: dict[str, dict[str, Any]] = {}
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "llm_workflow_case_result":
                continue
            case_result = event.get("case_result") or event.get("result")
            if not isinstance(case_result, dict):
                case_result = {
                    "name": event.get("case"),
                    "status": event.get("status"),
                    "case_passed": event.get("case_passed"),
                    "failed_stage": event.get("failed_stage"),
                    "stage_trace": event.get("stage_trace", []),
                }
            if case_result.get("status") != "completed":
                continue
            by_name[str(case_result.get("name"))] = case_result

        resumable: list[dict[str, Any]] = []
        for case in selected_cases:
            case_name = case["name"]
            prior = by_name.get(case_name)
            if not prior:
                break
            resumable.append(prior)
        return resumable

    def _append_llm_trace(
        self,
        trace_path: Path | None,
        case_result: dict[str, Any],
        summary: dict[str, Any],
    ) -> None:
        if trace_path is None:
            return
        event = {
            "type": "llm_workflow_case_result",
            "case": case_result.get("name"),
            "status": case_result.get("status"),
            "case_passed": case_result.get("case_passed"),
            "failed_stage": case_result.get("failed_stage"),
            "stage_trace": case_result.get("stage_trace", []),
            "case_result": case_result,
            "summary": {
                "completed_cases": summary.get("completed_cases"),
                "remaining_cases": summary.get("remaining_cases"),
                "end_to_end_pass_rate": summary.get("end_to_end_pass_rate"),
                "fit_label_accuracy": summary.get("fit_label_accuracy"),
                "tailor_pass_rate": summary.get("tailor_pass_rate"),
            },
        }
        with trace_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def _record_stage(
        self,
        stage_trace: list[dict[str, Any]],
        stage: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        stage_trace.append(
            {
                "stage": stage,
                "status": status,
                "time_ms": int(time.perf_counter() * 1000),
                "details": details or {},
            }
        )

    def _compact_context_summary(self, context: dict[str, Any] | None) -> dict[str, Any] | None:
        if not context:
            return None
        return {
            "strategy": context.get("strategy"),
            "raw_chars": context.get("raw_chars"),
            "compressed_chars": context.get("compressed_chars"),
            "reduction_ratio": context.get("reduction_ratio"),
            "expansion_ratio": context.get("expansion_ratio"),
            "retained_evidence_count": context.get("retained_evidence_count"),
            "levels": [
                {
                    "name": item.get("name"),
                    "output_chars": item.get("output_chars"),
                    "budget_chars": item.get("budget_chars"),
                    "within_budget": item.get("within_budget"),
                    "events": item.get("events", []),
                }
                for item in context.get("levels", [])
            ],
        }

    async def _run_llm_workflow_case(
        self,
        db: Session,
        case: dict[str, Any],
        *,
        evaluation_run_id: int | None = None,
    ) -> dict[str, Any]:
        stage = "start"
        case_started = time.perf_counter()
        stage_trace: list[dict[str, Any]] = []
        result: dict[str, Any] = {
            "name": case["name"],
            "difficulty": case.get("difficulty", "unknown"),
            "expected_fit_label": case["expected_fit_label"],
            "expected_fit_score_range": case.get("expected_fit_score_range"),
            "run_tailor": bool(case.get("run_tailor")),
            "status": "running",
            "stage_trace": stage_trace,
        }
        try:
            stage = "resume_parse"
            self._record_stage(stage_trace, stage, "started")
            parser = ResumeParserService()
            with llm_trace_context(evaluation_run_id=evaluation_run_id, case_name=case["name"], stage=stage):
                profile_json = await parser.parse_structured_resume(case["resume_raw_text"], db=db)
            profile_text = json.dumps(profile_json, ensure_ascii=False)
            profile_skill_recall = self._keyword_hit_rate(profile_text, case.get("expected_profile_skills", []))
            profile_keyword_hit_rate = self._keyword_hit_rate(profile_text, case.get("expected_profile_keywords", []))
            profile = Profile(
                name=profile_json.get("name") or case["name"],
                email=profile_json.get("email"),
                phone=profile_json.get("phone"),
                headline=profile_json.get("headline"),
                target_roles_json=profile_json.get("target_roles", []),
                source_type="llm_eval",
                raw_resume_text=case["resume_raw_text"],
                structured_profile_json=profile_json,
            )
            db.add(profile)
            db.commit()
            db.refresh(profile)
            profile_chunks = ResumeTextSplitter().build_resume_chunks(profile_json)
            profile_chunk_count = SQLiteVectorIndex().upsert_profile_chunks(db, profile.id, profile_chunks)
            result.update(
                {
                    "profile_id": profile.id,
                    "resume_parse_success": True,
                    "profile_skill_recall": profile_skill_recall,
                    "profile_keyword_hit_rate": profile_keyword_hit_rate,
                    "profile_chunk_count": profile_chunk_count,
                }
            )
            self._record_stage(
                stage_trace,
                stage,
                "completed",
                {
                    "profile_id": profile.id,
                    "name": profile_json.get("name"),
                    "skills": profile_json.get("skills", [])[:12],
                    "project_count": len(profile_json.get("projects", [])),
                    "profile_skill_recall": profile_skill_recall,
                    "profile_keyword_hit_rate": profile_keyword_hit_rate,
                    "profile_chunk_count": profile_chunk_count,
                },
            )

            stage = "jd_parse"
            self._record_stage(stage_trace, stage, "started")
            job_payload = case["job"]
            with llm_trace_context(evaluation_run_id=evaluation_run_id, case_name=case["name"], stage=stage):
                jd = await JDParserService().parse_jd(
                    job_payload["jd_text"],
                    title=job_payload.get("title"),
                    company=job_payload.get("company"),
                    db=db,
                )
            jd_text = json.dumps(jd, ensure_ascii=False)
            jd_skill_recall = self._keyword_hit_rate(jd_text, case.get("expected_jd_skills", []))
            job = Job(
                source="llm_eval",
                external_id=f"llm_eval:{case['name']}:{profile.id}",
                title=job_payload.get("title") or jd.get("title") or "LLM Eval Job",
                company=job_payload.get("company"),
                raw_jd_text=job_payload["jd_text"],
                structured_jd_json=jd,
                apply_url="https://example.com/jobs/llm-eval",
            )
            db.add(job)
            db.commit()
            db.refresh(job)
            jd_chunks = ResumeTextSplitter().split_jd_text(job.raw_jd_text, job.structured_jd_json, prefix=f"llm_job_{job.id}")
            job_chunk_count = SQLiteVectorIndex().upsert_job_chunks(db, job.id, jd_chunks)
            result.update(
                {
                    "job_id": job.id,
                    "jd_parse_success": True,
                    "jd_skill_recall": jd_skill_recall,
                    "job_chunk_count": job_chunk_count,
                }
            )
            self._record_stage(
                stage_trace,
                stage,
                "completed",
                {
                    "job_id": job.id,
                    "title": job.title,
                    "company": job.company,
                    "required_skills": jd.get("required_skills", [])[:12],
                    "jd_skill_recall": jd_skill_recall,
                    "job_chunk_count": job_chunk_count,
                },
            )

            stage = "match_and_retrieve"
            self._record_stage(stage_trace, stage, "started")
            match = self.matcher.create_match_result(db, profile, job)
            expected_evidence_keywords = list(
                dict.fromkeys(
                    case.get("expected_profile_keywords", []) + case.get("expected_tailored_keywords", [])
                )
            )
            evidence_text = json.dumps(match.relevant_evidence_json, ensure_ascii=False)
            evidence_hit_rate = self._keyword_hit_rate(evidence_text, expected_evidence_keywords)
            result.update(
                {
                    "match_result_id": match.id,
                    "matcher_overall_score": match.overall_score,
                    "matcher_evidence_hit_rate": evidence_hit_rate,
                }
            )
            self._record_stage(
                stage_trace,
                stage,
                "completed",
                {
                    "match_result_id": match.id,
                    "overall_score": match.overall_score,
                    "matched_skills": match.matched_skills_json,
                    "missing_skills": match.missing_skills_json,
                    "evidence_hit_rate": evidence_hit_rate,
                    "top_evidence": [
                        {
                            "chunk_uid": item.get("chunk_uid"),
                            "chunk_type": item.get("chunk_type"),
                            "evidence_type": item.get("evidence_type"),
                            "polarity": item.get("polarity"),
                            "score": item.get("score"),
                            "text_preview": str(item.get("text") or "")[:220],
                        }
                        for item in (match.relevant_evidence_json or [])[:5]
                    ],
                },
            )

            stage = "fit_judge"
            self._record_stage(stage_trace, stage, "started")
            with llm_trace_context(evaluation_run_id=evaluation_run_id, case_name=case["name"], stage=stage):
                suitability = await self._llm_judge_suitability(db, profile.structured_profile_json, job)
            fit_context_compression = suitability.pop("_context_compression", None)
            predicted_label = str(suitability.get("fit_label") or "").strip()
            fit_score = self._coerce_float(suitability.get("fit_score"))
            range_error = self._score_range_error(fit_score, case.get("expected_fit_score_range"))
            result.update(
                {
                    "fit_judge_success": True,
                    "predicted_fit_label": predicted_label,
                    "label_passed": predicted_label == case["expected_fit_label"],
                    "predicted_fit_score": fit_score,
                    "fit_score_range_error": range_error,
                    "fit_score_in_expected_range": range_error == 0,
                    "fit_context_compression": fit_context_compression,
                    "suitability": suitability,
                }
            )
            self._record_stage(
                stage_trace,
                stage,
                "completed",
                {
                    "expected_fit_label": case["expected_fit_label"],
                    "predicted_fit_label": predicted_label,
                    "label_passed": predicted_label == case["expected_fit_label"],
                    "predicted_fit_score": fit_score,
                    "fit_score_range_error": range_error,
                    "message_preview": str(suitability.get("message_to_candidate") or "")[:240],
                    "context_compression": self._compact_context_summary(fit_context_compression),
                },
            )

            if case.get("run_tailor"):
                stage = "tailor_resume"
                self._record_stage(stage_trace, stage, "started")
                with llm_trace_context(evaluation_run_id=evaluation_run_id, case_name=case["name"], stage=stage):
                    version = await ResumeTailorService().tailor_resume(db, profile, job)
                resume_text = version.tailored_resume_markdown
                tailored_keyword_hit_rate = self._keyword_hit_rate(
                    resume_text,
                    case.get("expected_tailored_keywords", []),
                )
                forbidden_claims = self._forbidden_claim_hits(
                    resume_text,
                    case.get("forbidden_tailored_claims", []),
                )
                verification = version.verification_json or ResumeGuardrailService().verify(
                    profile=profile,
                    job=job,
                    resume_markdown=resume_text,
                    evidence=version.source_evidence_json,
                )
                keyword_alignment = version.keyword_alignment_json or {}
                react_repair = keyword_alignment.get("react_repair")
                tailor_passed = (
                    verification.get("passed", False)
                    and tailored_keyword_hit_rate >= 0.6
                    and not forbidden_claims
                )
                result.update(
                    {
                        "resume_version_id": version.id,
                        "tailor_success": True,
                        "tailor_passed": tailor_passed,
                        "tailored_keyword_hit_rate": tailored_keyword_hit_rate,
                        "forbidden_claim_hits": forbidden_claims,
                        "forbidden_claim_free": not forbidden_claims,
                        "guardrail_passed": bool(verification.get("passed")),
                        "tailored_risk_level": verification.get("risk_level"),
                        "hallucination_count": verification.get("hallucination_count", 0),
                        "jd_keyword_coverage_score": verification.get("jd_keyword_coverage_score", 0),
                        "tailor_context_compression": keyword_alignment.get("context_compression"),
                        "tailor_react_repair": react_repair,
                        "resume_preview": resume_text[:600],
                    }
                )
                self._record_stage(
                    stage_trace,
                    stage,
                    "completed",
                    {
                        "resume_version_id": version.id,
                        "tailor_passed": tailor_passed,
                        "tailored_keyword_hit_rate": tailored_keyword_hit_rate,
                        "guardrail_passed": bool(verification.get("passed")),
                        "risk_level": verification.get("risk_level"),
                        "hallucination_count": verification.get("hallucination_count", 0),
                        "context_compression": self._compact_context_summary(
                            keyword_alignment.get("context_compression")
                        ),
                        "react_repair": react_repair,
                        "resume_preview": resume_text[:360],
                    },
                )
            else:
                result.update(
                    {
                        "tailor_success": None,
                        "tailor_passed": None,
                        "tailored_keyword_hit_rate": None,
                        "forbidden_claim_hits": [],
                        "forbidden_claim_free": None,
                        "guardrail_passed": None,
                        "hallucination_count": None,
                    }
                )
                self._record_stage(stage_trace, "tailor_resume", "skipped", {"reason": "case.run_tailor=false"})

            result["status"] = "completed"
            result["case_passed"] = self._llm_case_passed(result)
            result["latency_ms"] = int((time.perf_counter() - case_started) * 1000)
            self._record_stage(
                stage_trace,
                "case",
                "completed",
                {"case_passed": result["case_passed"], "latency_ms": result["latency_ms"]},
            )
            return result
        except Exception as exc:  # noqa: BLE001
            self._record_stage(stage_trace, stage, "failed", {"error": format_exception(exc)})
            result.update(
                {
                    "status": "failed",
                    "failed_stage": stage,
                    "error": format_exception(exc),
                    "case_passed": False,
                    "latency_ms": int((time.perf_counter() - case_started) * 1000),
                }
            )
            return result

    def _agent_request(self, **kwargs: Any) -> AgentRunRequest:
        return AgentRunRequest.model_validate(kwargs)

    def _ranking_margin(self, matches: list[dict[str, Any]]) -> float:
        if len(matches) < 2:
            return float(matches[0].get("overall_score") or 0) if matches else 0.0
        return round(float(matches[0].get("overall_score") or 0) - float(matches[1].get("overall_score") or 0), 4)

    def _run_has_completed_plan(self, db: Session, run_id: int) -> bool:
        step = (
            db.query(AgentStep)
            .filter(AgentStep.run_id == run_id, AgentStep.step_name == "plan_task")
            .first()
        )
        return bool(step and step.status == "completed")

    def _run_has_artifact(self, db: Session, run_id: int, artifact_type: str) -> bool:
        return (
            db.query(AgentArtifact)
            .filter(AgentArtifact.run_id == run_id, AgentArtifact.artifact_type == artifact_type)
            .first()
            is not None
        )

    def _agent_run_trace(self, db: Session, run_id: int) -> dict[str, Any]:
        steps = (
            db.query(AgentStep)
            .filter(AgentStep.run_id == run_id)
            .order_by(AgentStep.created_at.asc(), AgentStep.id.asc())
            .all()
        )
        artifacts = (
            db.query(AgentArtifact)
            .filter(AgentArtifact.run_id == run_id)
            .order_by(AgentArtifact.created_at.asc(), AgentArtifact.id.asc())
            .all()
        )
        return {
            "run_id": run_id,
            "steps": [
                {
                    "step_name": step.step_name,
                    "tool_name": step.tool_name,
                    "status": step.status,
                    "latency_ms": step.latency_ms,
                    "error_message": step.error_message,
                }
                for step in steps
            ],
            "artifacts": [artifact.artifact_type for artifact in artifacts],
        }

    def _agent_full_flow_case_passed(self, result: dict[str, Any], case: dict[str, Any]) -> bool:
        if result.get("status") != "completed":
            return False
        base = (
            bool(result.get("top_job_passed"))
            and bool(result.get("score_passed"))
            and bool(result.get("trace_passed"))
            and bool(result.get("artifact_passed"))
            and bool(result.get("langgraph_passed"))
        )
        if not base:
            return False
        if case.get("run_tailor") and result.get("tailor_passed") is not True:
            return False
        if case.get("run_quick_apply") and result.get("quick_apply_passed") is not True:
            return False
        if (
            case.get("run_quick_apply")
            and not case.get("expect_quick_apply_blocked")
            and result.get("application_packet_passed") is not True
        ):
            return False
        return True

    def _summarize_agent_full_flow(self, case_results: list[dict[str, Any]], dataset_path: Path) -> dict[str, Any]:
        count = max(len(case_results), 1)
        tailor_cases = [item for item in case_results if item.get("tailor_passed") is not None]
        quick_cases = [item for item in case_results if item.get("quick_apply_passed") is not None]
        packet_cases = [item for item in case_results if item.get("application_packet_passed") is not None]
        blocked_cases = [item for item in case_results if item.get("fit_gate_blocked") is True]
        return {
            "evaluation_type": "agent_full_flow",
            "dataset": dataset_path.name,
            "case_count": len(case_results),
            "pass_rate": round(sum(1 for item in case_results if item.get("case_passed")) / count, 4),
            "completed_rate": round(sum(1 for item in case_results if item.get("status") == "completed") / count, 4),
            "top_job_accuracy": self._avg_bool(case_results, "top_job_passed"),
            "score_gate_accuracy": self._avg_bool(case_results, "score_passed"),
            "tailor_pass_rate": self._avg_bool(tailor_cases, "tailor_passed"),
            "quick_apply_pass_rate": self._avg_bool(quick_cases, "quick_apply_passed"),
            "application_packet_pass_rate": self._avg_bool(packet_cases, "application_packet_passed"),
            "fit_gate_block_count": len(blocked_cases),
            "trace_pass_rate": self._avg_bool(case_results, "trace_passed"),
            "artifact_pass_rate": self._avg_bool(case_results, "artifact_passed"),
            "langgraph_pass_rate": self._avg_bool(case_results, "langgraph_passed"),
            "avg_top_job_score": self._avg_number(case_results, "top_job_score"),
            "avg_ranking_margin": self._avg_number(case_results, "ranking_margin"),
            "failure_breakdown": self._agent_full_flow_failure_breakdown(case_results),
            "notes": [
                "覆盖 find_jobs_for_profile、tailor_resume_for_job、quick_apply、Trace、Artifact、RAG 证据和 Guardrail。",
                "所有 Agent run 必须通过 LangGraph 主编排，input/output/execution_plan 均需标记 orchestration_framework=langgraph。",
                "岗位源使用可控评测源，避免外部招聘站波动影响全链路回归；真实岗位抓取由 job source 单独测试。",
                "低匹配 quick_apply 应被 fit_gate 阻断，失败直接写入 Agent step trace。",
            ],
        }

    def _run_uses_langgraph(self, run) -> bool:
        output = run.output_json or {}
        plan = output.get("execution_plan") or {}
        return (
            (run.input_json or {}).get("orchestration_framework") == "langgraph"
            and output.get("orchestration_framework") == "langgraph"
            and plan.get("orchestration_framework") == "langgraph"
            and bool(plan.get("graph_thread_id"))
        )

    def _summarize_real_job_source_smoke(
        self,
        case_results: list[dict[str, Any]],
        *,
        query: str,
        location: str | None,
        limit: int,
        source_names: list[str],
        latency_ms: int,
    ) -> dict[str, Any]:
        source_count = max(len(source_names), 1)
        total_result_count = sum(int(item.get("result_count") or 0) for item in case_results)
        reachable_count = sum(1 for item in case_results if item.get("source_reachable"))
        result_source_count = sum(1 for item in case_results if item.get("has_results"))
        source_error_count = sum(1 for item in case_results if item.get("status") == "source_error")
        non_empty_jd_count = sum(int(item.get("non_empty_jd_count") or 0) for item in case_results)
        apply_url_count = sum(int(item.get("apply_url_count") or 0) for item in case_results)
        internship_like_count = sum(int(item.get("internship_like_count") or 0) for item in case_results)
        query_relevant_count = sum(int(item.get("query_relevant_count") or 0) for item in case_results)
        agent_related_count = sum(int(item.get("agent_related_count") or 0) for item in case_results)
        relevance_score_sum = sum(float(item.get("relevance_score_sum") or 0.0) for item in case_results)
        relevance_score_count = sum(int(item.get("relevance_score_count") or 0) for item in case_results)
        top_relevance_scores = [
            float(item.get("top_relevance_score") or 0.0)
            for item in case_results
            if int(item.get("result_count") or 0) > 0
        ]
        if source_error_count > 0 and total_result_count > 0:
            status = "completed_with_source_errors"
        elif (
            reachable_count == len(source_names)
            and result_source_count == len(source_names)
            and total_result_count > 0
        ):
            status = "completed"
        elif reachable_count == len(source_names) and total_result_count > 0:
            status = "completed_with_empty_sources"
        elif total_result_count > 0:
            status = "completed_with_source_errors"
        else:
            status = "source_unavailable"
        result_denominator = max(total_result_count, 1)
        return {
            "evaluation_type": "real_job_source_smoke",
            "status": status,
            "query": query,
            "location": location,
            "limit": limit,
            "sources": source_names,
            "source_count": len(source_names),
            "reachable_source_rate": round(reachable_count / source_count, 4),
            "result_source_rate": round(result_source_count / source_count, 4),
            "source_error_count": source_error_count,
            "total_result_count": total_result_count,
            "non_empty_jd_rate": round(non_empty_jd_count / result_denominator, 4),
            "apply_url_rate": round(apply_url_count / result_denominator, 4),
            "internship_like_rate": round(internship_like_count / result_denominator, 4),
            "query_relevance_rate": round(query_relevant_count / result_denominator, 4),
            "agent_related_rate": round(agent_related_count / result_denominator, 4),
            "avg_relevance_score": round(relevance_score_sum / max(relevance_score_count, 1), 4),
            "avg_top_relevance_score": round(sum(top_relevance_scores) / max(len(top_relevance_scores), 1), 4),
            "latency_ms": latency_ms,
            "source_errors": {
                item["source"]: item.get("error")
                for item in case_results
                if item.get("status") == "source_error"
            },
            "core_regression_independent": True,
            "notes": [
                "真实岗位源 smoke 只衡量 source 层健康度，不参与 agent_full_flow 的核心 pass_rate。",
                "网络失败、招聘站空结果或接口变化会记录为 source_error/source_unavailable，而不是被静默吞掉。",
                "岗位质量用 JD 非空、apply_url、internship-like、query relevance、Agent/AI relevance 和 relevance score 衡量。",
            ],
        }

    def _summarize_interview_source_smoke(
        self,
        case_results: list[dict[str, Any]],
        *,
        query: str,
        limit: int,
        source_names: list[str],
        latency_ms: int,
    ) -> dict[str, Any]:
        source_count = max(len(source_names), 1)
        total_result_count = sum(int(item.get("result_count") or 0) for item in case_results)
        reachable_count = sum(1 for item in case_results if item.get("source_reachable"))
        result_source_count = sum(1 for item in case_results if item.get("has_results"))
        source_error_count = sum(1 for item in case_results if item.get("status") == "source_error")
        url_count = sum(int(item.get("url_count") or 0) for item in case_results)
        interview_signal_count = sum(int(item.get("interview_signal_count") or 0) for item in case_results)
        query_relevant_count = sum(int(item.get("query_relevant_count") or 0) for item in case_results)
        content_extractable_count = sum(int(item.get("content_extractable_count") or 0) for item in case_results)
        denominator = max(total_result_count, 1)
        quality_passed = total_result_count > 0 and interview_signal_count > 0 and query_relevant_count > 0
        if source_error_count > 0 and total_result_count > 0:
            status = "completed_with_source_errors"
        elif reachable_count == len(source_names) and result_source_count == len(source_names) and quality_passed:
            status = "completed"
        elif reachable_count == len(source_names) and total_result_count > 0:
            status = "completed_with_low_quality_results"
        elif reachable_count == len(source_names):
            status = "completed_with_empty_sources"
        elif total_result_count > 0:
            status = "completed_with_source_errors"
        else:
            status = "source_unavailable"
        return {
            "evaluation_type": "interview_source_smoke",
            "status": status,
            "query": query,
            "limit": limit,
            "sources": source_names,
            "source_count": len(source_names),
            "reachable_source_rate": round(reachable_count / source_count, 4),
            "result_source_rate": round(result_source_count / source_count, 4),
            "source_error_count": source_error_count,
            "total_result_count": total_result_count,
            "url_rate": round(url_count / denominator, 4),
            "interview_signal_rate": round(interview_signal_count / denominator, 4),
            "query_relevance_rate": round(query_relevant_count / denominator, 4),
            "content_extractable_rate": round(content_extractable_count / denominator, 4),
            "latency_ms": latency_ms,
            "source_errors": {
                item["source"]: item.get("error")
                for item in case_results
                if item.get("status") == "source_error"
            },
            "source_empty": [
                item["source"]
                for item in case_results
                if item.get("source_reachable") and not item.get("has_results")
            ],
            "core_regression_independent": True,
            "notes": [
                "面经 source smoke 只衡量牛客网、OfferShow、小红书等外部内容源健康度，不参与 interview_prep 核心 pass_rate。",
                "登录、反爬、客户端渲染、空结果和低质量搜索结果都会显式进入 source 层指标。",
                "核心面试包仍依赖已导入面经和可重复评测；真实平台抓取结果只能作为增强证据。",
            ],
        }

    async def _run_jd_parser_case(self, db: Session, case: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        expected_required = [str(item) for item in case.get("expected_required_skills", [])]
        expected_keywords = [str(item) for item in case.get("expected_keywords", [])]
        expected_absent = [str(item) for item in case.get("expected_absent_required_skills", [])]
        min_required_recall = float(case.get("min_required_skill_recall", 0.75))
        min_keyword_hit = float(case.get("min_keyword_hit_rate", 0.7))
        min_responsibility_count = int(case.get("min_responsibility_count", 1))
        min_qualification_count = int(case.get("min_qualification_count", 1))
        result: dict[str, Any] = {
            "name": case["name"],
            "difficulty": case.get("difficulty", "unknown"),
            "noise_profiles": case.get("noise_profiles", []),
            "title": case.get("title"),
            "company": case.get("company"),
            "expected_job_type": case.get("expected_job_type"),
            "expected_required_skills": expected_required,
            "expected_absent_required_skills": expected_absent,
            "status": "running",
        }
        try:
            parsed = await self.jd_parser.parse_jd(
                case["jd_text"],
                title=case.get("title"),
                company=case.get("company"),
                location=case.get("location"),
                db=db,
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            result.update(
                {
                    "status": "parse_error",
                    "case_passed": False,
                    "error": format_exception(exc),
                    "failed_checks": ["parse_error"],
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                }
            )
            return result

        parsed_required = [str(item) for item in parsed.get("required_skills", []) if str(item).strip()]
        parsed_preferred = [str(item) for item in parsed.get("preferred_skills", []) if str(item).strip()]
        parsed_text = json.dumps(parsed, ensure_ascii=False)
        required_hits = self._normalized_hits(parsed_required, expected_required)
        required_skill_recall = self._recall(
            {self._normalize_eval_term(item) for item in parsed_required},
            {self._normalize_eval_term(item) for item in expected_required},
        )
        keyword_hit_rate = self._keyword_hit_rate(parsed_text, expected_keywords)
        absent_violations = [
            item
            for item in expected_absent
            if self._normalize_eval_term(item) in {self._normalize_eval_term(skill) for skill in parsed_required}
        ]
        job_type_passed = True
        if case.get("expected_job_type"):
            job_type_passed = str(parsed.get("job_type") or "").lower() == str(case["expected_job_type"]).lower()
        responsibility_count = len(parsed.get("responsibilities", []) or [])
        qualification_count = len(parsed.get("qualifications", []) or [])
        responsibility_min_passed = responsibility_count >= min_responsibility_count
        qualification_min_passed = qualification_count >= min_qualification_count
        failed_checks = []
        if required_skill_recall < min_required_recall:
            failed_checks.append("required_skill_recall")
        if keyword_hit_rate < min_keyword_hit:
            failed_checks.append("keyword_hit_rate")
        if not job_type_passed:
            failed_checks.append("job_type")
        if not responsibility_min_passed:
            failed_checks.append("responsibility_count")
        if not qualification_min_passed:
            failed_checks.append("qualification_count")
        if absent_violations:
            failed_checks.append("absent_required_skill_violation")
        result.update(
            {
                "status": "completed",
                "case_passed": not failed_checks,
                "failed_checks": failed_checks,
                "parser_mode": self._jd_parser_mode(),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "parsed_job_type": parsed.get("job_type"),
                "job_type_passed": job_type_passed,
                "parsed_required_skills": parsed_required,
                "parsed_preferred_skills": parsed_preferred,
                "parsed_keywords_preview": (parsed.get("keywords") or [])[:16],
                "required_skill_recall": required_skill_recall,
                "required_skill_hits": required_hits,
                "missing_required_skills": [
                    item
                    for item in expected_required
                    if self._normalize_eval_term(item)
                    not in {self._normalize_eval_term(skill) for skill in parsed_required}
                ],
                "keyword_hit_rate": keyword_hit_rate,
                "absent_required_skill_violations": absent_violations,
                "responsibility_count": responsibility_count,
                "qualification_count": qualification_count,
                "responsibility_min_passed": responsibility_min_passed,
                "qualification_min_passed": qualification_min_passed,
            }
        )
        return result

    def _jd_parser_mode(self) -> str:
        if self.jd_parser.llm.available:
            return "llm"
        if self.settings.llm_fallback_enabled:
            return "heuristic_fallback"
        return "llm_required_unavailable"

    def _summarize_jd_parser(self, case_results: list[dict[str, Any]], dataset_path: Path) -> dict[str, Any]:
        count = max(len(case_results), 1)
        completed = [item for item in case_results if item.get("status") == "completed"]
        quality_failures = [item for item in completed if not item.get("case_passed")]
        parse_errors = [item for item in case_results if item.get("status") == "parse_error"]
        if parse_errors and not completed:
            status = "parser_unavailable"
        elif parse_errors or quality_failures:
            status = "completed_with_quality_failures"
        else:
            status = "completed"
        return {
            "evaluation_type": "jd_parser",
            "status": status,
            "dataset": dataset_path.name,
            "case_count": len(case_results),
            "completed_rate": round(len(completed) / count, 4),
            "pass_rate": round(sum(1 for item in case_results if item.get("case_passed")) / count, 4),
            "avg_required_skill_recall": self._avg_number(case_results, "required_skill_recall"),
            "avg_keyword_hit_rate": self._avg_number(case_results, "keyword_hit_rate"),
            "job_type_accuracy": self._avg_bool(completed, "job_type_passed"),
            "responsibility_min_pass_rate": self._avg_bool(completed, "responsibility_min_passed"),
            "qualification_min_pass_rate": self._avg_bool(completed, "qualification_min_passed"),
            "absent_required_skill_violation_count": sum(
                len(item.get("absent_required_skill_violations") or []) for item in case_results
            ),
            "avg_required_skill_count": self._avg_list_length(case_results, "parsed_required_skills"),
            "avg_preferred_skill_count": self._avg_list_length(case_results, "parsed_preferred_skills"),
            "parser_mode_counts": self._count_by_key(completed, "parser_mode"),
            "failure_breakdown": self._count_failed_checks(case_results),
            "difficulty_breakdown": self._summarize_jd_parser_by_key(case_results, "difficulty"),
            "noise_breakdown": self._summarize_jd_parser_by_key(case_results, "noise_profiles"),
            "notes": [
                "该评测独立衡量 JD parser 的结构化质量，避免真实 JD ingest 只看到 parse_success 却漏掉核心技能。",
                "case 同时检查 required skill recall、关键词命中、岗位类型、职责/要求覆盖和负向技能误抽取。",
                "生产配置下 LLM 不可用且未显式开启 fallback 时会记录 parse_error；测试环境可显式用 heuristic_fallback 做离线回归。",
            ],
        }

    def _normalized_hits(self, predicted: list[str], expected: list[str]) -> list[str]:
        predicted_terms = {self._normalize_eval_term(item) for item in predicted}
        return [item for item in expected if self._normalize_eval_term(item) in predicted_terms]

    def _normalize_eval_term(self, value: str) -> str:
        lowered = str(value or "").strip().lower()
        alias = {
            "retrieval augmented generation": "rag",
            "large language model": "llm",
            "large language models": "llm",
            "vector store": "vector database",
            "vector search": "vector database",
            "ab testing": "a b testing",
            "a/b testing": "a b testing",
            "a/b test": "a b testing",
            "cross encoder": "reranker",
        }
        lowered = alias.get(lowered, lowered)
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", lowered).strip()

    def _avg_list_length(self, rows: list[dict[str, Any]], key: str) -> float:
        if not rows:
            return 0.0
        return round(sum(len(item.get(key) or []) for item in rows) / len(rows), 4)

    def _count_jd_parser_failures(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        return self._count_failed_checks(rows)

    def _count_failed_checks(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            for check in row.get("failed_checks") or []:
                counts[str(check)] = counts.get(str(check), 0) + 1
        return counts

    def _summarize_jd_parser_by_key(self, rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            raw_value = row.get(key)
            values = raw_value if isinstance(raw_value, list) else [raw_value or "unknown"]
            for value in values or ["unknown"]:
                grouped.setdefault(str(value or "unknown"), []).append(row)
        return {
            group: {
                "case_count": len(items),
                "pass_rate": round(
                    sum(1 for item in items if item.get("case_passed")) / max(len(items), 1),
                    4,
                ),
                "avg_required_skill_recall": self._avg_number(items, "required_skill_recall"),
                "avg_keyword_hit_rate": self._avg_number(items, "keyword_hit_rate"),
                "absent_required_skill_violation_count": sum(
                    len(item.get("absent_required_skill_violations") or []) for item in items
                ),
            }
            for group, items in sorted(grouped.items())
        }

    def _run_application_packet_case(self, case: dict[str, Any]) -> dict[str, Any]:
        profile_data = case["profile"]
        job_data = case["job"]
        packet = case["packet"]
        profile = Profile(
            name=profile_data.get("name"),
            source_type="eval",
            raw_resume_text=profile_data.get("raw_resume_text") or "",
            structured_profile_json=profile_data.get("structured_profile") or {},
        )
        job = Job(
            source="eval",
            external_id=case["name"],
            title=job_data["title"],
            company=job_data.get("company"),
            apply_url=job_data.get("apply_url"),
            raw_jd_text=job_data.get("jd_text") or job_data["title"],
            structured_jd_json=job_data.get("structured_jd") or {},
        )
        validation = self.application_guardrail.validate(
            profile=profile,
            job=job,
            resume_version=None,
            cover_letter=packet.get("cover_letter") or "",
            outreach_message=packet.get("outreach_message") or "",
            checklist=packet.get("checklist") or [],
            automation_result=packet.get("automation_result") or {},
        )
        expected_passed = bool(case.get("expected_passed"))
        expected_issue_codes = set(str(item) for item in case.get("expected_issue_codes") or [])
        actual_issue_codes = {str(item.get("code")) for item in validation.get("issues") or []}
        expected_issues_hit = expected_issue_codes <= actual_issue_codes
        case_passed = validation["passed"] == expected_passed and expected_issues_hit
        if expected_passed and not validation["passed"]:
            case_passed = False
        return {
            "name": case["name"],
            "difficulty": case.get("difficulty", "unknown"),
            "noise_profile": case.get("noise_profile", "unknown"),
            "expected_passed": expected_passed,
            "expected_issue_codes": sorted(expected_issue_codes),
            "actual_passed": validation["passed"],
            "risk_level": validation["risk_level"],
            "actual_issue_codes": sorted(actual_issue_codes),
            "warning_codes": [str(item.get("code")) for item in validation.get("warnings") or []],
            "expected_issue_codes_hit": expected_issues_hit,
            "case_passed": case_passed,
            "validation": validation,
        }

    def _summarize_application_packet(
        self,
        case_results: list[dict[str, Any]],
        dataset_path: Path,
    ) -> dict[str, Any]:
        count = max(len(case_results), 1)
        expected_block = [item for item in case_results if not item.get("expected_passed")]
        expected_pass = [item for item in case_results if item.get("expected_passed")]
        false_blocks = [item for item in expected_pass if not item.get("actual_passed")]
        missed_blocks = [item for item in expected_block if item.get("actual_passed")]
        issue_expectations = [item for item in expected_block if item.get("expected_issue_codes")]
        return {
            "evaluation_type": "application_packet_guardrail",
            "status": "completed" if all(item.get("case_passed") for item in case_results) else "completed_with_quality_failures",
            "dataset": dataset_path.name,
            "case_count": len(case_results),
            "pass_rate": round(sum(1 for item in case_results if item.get("case_passed")) / count, 4),
            "high_risk_recall": round(
                sum(1 for item in expected_block if not item.get("actual_passed")) / max(len(expected_block), 1),
                4,
            ),
            "false_block_count": len(false_blocks),
            "missed_high_risk_count": len(missed_blocks),
            "issue_code_hit_rate": round(
                sum(1 for item in issue_expectations if item.get("expected_issue_codes_hit"))
                / max(len(issue_expectations), 1),
                4,
            ),
            "avg_warning_count": round(
                sum(len(item.get("warning_codes") or []) for item in case_results) / count,
                4,
            ),
            "risk_level_counts": self._count_by_key(case_results, "risk_level"),
            "failure_breakdown": self._count_application_packet_failures(case_results),
            "difficulty_breakdown": self._summarize_application_packet_by_key(case_results, "difficulty"),
            "noise_breakdown": self._summarize_application_packet_by_key(case_results, "noise_profile"),
            "notes": [
                "该评测只验证投递包质量 guardrail，不调用外部招聘站，也不调用 LLM。",
                "好包应通过，编造技能、缺目标岗位或越过人工确认边界的投递包应被阻断。",
                "missing_apply_url 和短外联文案目前作为 warning，不直接阻断投递包生成。",
            ],
        }

    def _run_interview_prep_case(self, db: Session, case: dict[str, Any], *, namespace: str) -> dict[str, Any]:
        profile_data = case["profile"]
        job_data = case["job"]
        profile = Profile(
            name=profile_data.get("name"),
            headline=profile_data.get("headline"),
            target_roles_json=profile_data.get("target_roles") or ["Agent 开发实习生"],
            source_type="eval_interview_prep",
            raw_resume_text=profile_data.get("raw_resume_text") or "",
            structured_profile_json=profile_data.get("structured_profile") or {},
        )
        job = Job(
            source="eval_interview_prep",
            external_id=f"{namespace}:{case['name']}",
            title=job_data["title"],
            company=job_data.get("company"),
            location=job_data.get("location"),
            job_type=job_data.get("job_type") or "实习",
            apply_url=job_data.get("apply_url"),
            raw_jd_text=job_data.get("jd_text") or job_data["title"],
            structured_jd_json=job_data.get("structured_jd") or {},
        )
        db.add_all([profile, job])
        db.commit()
        db.refresh(profile)
        db.refresh(job)
        chunks = ResumeTextSplitter().build_resume_chunks(profile.structured_profile_json)
        self.vector_index.upsert_profile_chunks(db, profile.id, chunks)
        experience_ids = []
        for source_item in case.get("interview_experiences") or []:
            experience = self.interview_prep_service.experience_service.create_experience(
                db,
                job=job if source_item.get("attach_to_job", True) else None,
                source_site=str(source_item.get("source_site") or "manual"),
                source_url=source_item.get("source_url"),
                title=source_item.get("title"),
                company=source_item.get("company") or job.company,
                role_keyword=source_item.get("role_keyword") or job.title,
                raw_text=str(source_item.get("raw_text") or ""),
            )
            experience_ids.append(experience.id)
        prep = asyncio.run(
            self.interview_prep_service.create_interview_prep_with_llm(
                db,
                profile=profile,
                job=job,
                experience_ids=experience_ids,
            )
        )

        categories = {group.get("category") for group in prep.question_sets_json or []}
        research_sites = {item.get("site") for item in prep.research_checklist_json or []}
        drill_skills = {str(item.get("skill") or "") for item in prep.gap_drills_json or []}
        questions = [question for group in prep.question_sets_json or [] for question in group.get("questions", [])]
        delivery_questions = self.interview_delivery.question_items(prep)
        question_ids = [item["question_id"] for item in delivery_questions]
        source_perspective_summary = self.interview_delivery.source_perspective_summary(prep)
        core_perspective_counts = source_perspective_summary.get("core_perspectives") or {}
        preparation_angle_counts = source_perspective_summary.get("preparation_angle_counts") or {}
        source_counts = source_perspective_summary.get("counts") or {}
        question_id_passed = (
            len(question_ids) == len(questions)
            and all(question_ids)
            and len(set(question_ids)) == len(question_ids)
        )
        source_perspective_passed = all(
            int(core_perspective_counts.get(key) or 0) > 0
            for key in ("online_experience", "resume_project_stack", "other_interview_questions")
        )
        preparation_angle_passed = all(
            int(preparation_angle_counts.get(key) or 0) > 0
            for key in (
                "same_role_interview_experience",
                "resume_project_tech_stack",
                "other_possible_interview_questions",
            )
        ) and len((prep.summary_json or {}).get("preparation_angles") or []) >= 3
        llm_question_generation_passed = (
            str(prep.generation_mode) == "langgraph_agentic_rag_v3_cost_guarded"
            and int(source_counts.get("llm_project_implementation") or 0) >= 2
            and int(source_counts.get("llm_foundation_drill") or 0) >= 2
        )
        question_quality = (prep.summary_json or {}).get("question_quality") or {}
        question_quality_passed = question_quality.get("passed") is True
        markdown = self.interview_delivery.render_markdown(prep)
        markdown_export_passed = (
            prep.title in markdown
            and "## 问题来源分布" in markdown
            and "## 准备角度" in markdown
            and "## 面经参考链接" in markdown
            and "连续追问" in markdown
            and "## 外部调研清单" in markdown
            and "## 证据边界" in markdown
        )
        question_text = json.dumps(questions, ensure_ascii=False)
        source_evidence = prep.source_evidence_json or []
        experience_sources = [
            item for item in source_evidence if item.get("evidence_type") == "interview_experience"
        ]
        experience_sites = {item.get("source_site") for item in experience_sources}
        expected_categories = set(case.get("expected_categories") or [])
        expected_research_sites = set(case.get("expected_research_sites") or [])
        expected_gap_drills = set(case.get("expected_gap_drills") or [])
        expected_experience_sites = set(case.get("expected_experience_sites") or [])
        expected_keywords = [str(item) for item in case.get("expected_question_keywords") or []]
        expected_source_backed_min = int(case.get("expected_source_backed_min") or 0)

        category_aliases = {
            "通用面试与行为问题": {"通用面试与行为问题", "工程协作与落地"},
        }
        category_passed = all(
            expected in categories
            or bool(category_aliases.get(str(expected), set()) & categories)
            for expected in expected_categories
        )
        research_passed = expected_research_sites <= research_sites
        gap_passed = expected_gap_drills <= drill_skills
        experience_site_passed = expected_experience_sites <= experience_sites
        source_backed_passed = (
            int((prep.coverage_json or {}).get("source_backed_question_count") or 0) >= expected_source_backed_min
        )
        keyword_hit_rate = self._keyword_hit_rate(question_text, expected_keywords)
        min_question_count = int(case.get("min_question_count") or 8)
        case_passed = (
            prep.coverage_json.get("passed") is True
            and category_passed
            and research_passed
            and gap_passed
            and experience_site_passed
            and source_backed_passed
            and question_id_passed
            and source_perspective_passed
            and preparation_angle_passed
            and llm_question_generation_passed
            and question_quality_passed
            and markdown_export_passed
            and len(questions) >= min_question_count
            and keyword_hit_rate >= float(case.get("min_keyword_hit_rate") or 0.6)
        )
        return {
            "name": case["name"],
            "difficulty": case.get("difficulty", "unknown"),
            "role_type": case.get("role_type", "unknown"),
            "case_passed": case_passed,
            "interview_prep_id": prep.id,
            "question_count": len(questions),
            "gap_drill_count": len(prep.gap_drills_json or []),
            "research_item_count": len(prep.research_checklist_json or []),
            "source_backed_experience_count": len(experience_sources),
            "source_backed_question_count": int((prep.coverage_json or {}).get("source_backed_question_count") or 0),
            "coverage": prep.coverage_json,
            "question_id_passed": question_id_passed,
            "source_perspective_passed": source_perspective_passed,
            "preparation_angle_passed": preparation_angle_passed,
            "llm_question_generation_passed": llm_question_generation_passed,
            "question_quality_passed": question_quality_passed,
            "question_quality_score": question_quality.get("score", 0.0),
            "question_quality": question_quality,
            "agentic_rag": (prep.summary_json or {}).get("agentic_rag") or {},
            "markdown_export_passed": markdown_export_passed,
            "source_perspective_summary": source_perspective_summary,
            "category_passed": category_passed,
            "research_passed": research_passed,
            "gap_passed": gap_passed,
            "experience_site_passed": experience_site_passed,
            "source_backed_passed": source_backed_passed,
            "keyword_hit_rate": keyword_hit_rate,
            "expected_categories": sorted(expected_categories),
            "actual_categories": sorted(str(item) for item in categories if item),
            "expected_research_sites": sorted(expected_research_sites),
            "actual_research_sites": sorted(str(item) for item in research_sites if item),
            "expected_gap_drills": sorted(expected_gap_drills),
            "actual_gap_drills": sorted(drill_skills),
            "expected_experience_sites": sorted(expected_experience_sites),
            "actual_experience_sites": sorted(str(item) for item in experience_sites if item),
        }

    def _summarize_interview_prep(
        self,
        case_results: list[dict[str, Any]],
        dataset_path: Path,
    ) -> dict[str, Any]:
        count = max(len(case_results), 1)
        return {
            "evaluation_type": "interview_prep",
            "status": "completed" if all(item.get("case_passed") for item in case_results) else "completed_with_quality_failures",
            "dataset": dataset_path.name,
            "case_count": len(case_results),
            "llm_mode": self.interview_evaluation_llm_mode,
            "pass_rate": round(sum(1 for item in case_results if item.get("case_passed")) / count, 4),
            "avg_question_count": self._avg_number(case_results, "question_count"),
            "avg_gap_drill_count": self._avg_number(case_results, "gap_drill_count"),
            "avg_research_item_count": self._avg_number(case_results, "research_item_count"),
            "avg_source_backed_experience_count": self._avg_number(case_results, "source_backed_experience_count"),
            "avg_source_backed_question_count": self._avg_number(case_results, "source_backed_question_count"),
            "category_pass_rate": self._avg_bool(case_results, "category_passed"),
            "research_source_pass_rate": self._avg_bool(case_results, "research_passed"),
            "gap_drill_pass_rate": self._avg_bool(case_results, "gap_passed"),
            "experience_site_pass_rate": self._avg_bool(case_results, "experience_site_passed"),
            "source_backed_pass_rate": self._avg_bool(case_results, "source_backed_passed"),
            "question_id_pass_rate": self._avg_bool(case_results, "question_id_passed"),
            "source_perspective_pass_rate": self._avg_bool(case_results, "source_perspective_passed"),
            "preparation_angle_pass_rate": self._avg_bool(case_results, "preparation_angle_passed"),
            "llm_question_generation_pass_rate": self._avg_bool(case_results, "llm_question_generation_passed"),
            "question_quality_pass_rate": self._avg_bool(case_results, "question_quality_passed"),
            "avg_question_quality_score": self._avg_number(case_results, "question_quality_score"),
            "markdown_export_pass_rate": self._avg_bool(case_results, "markdown_export_passed"),
            "avg_keyword_hit_rate": self._avg_number(case_results, "keyword_hit_rate"),
            "avg_required_skill_coverage_rate": self._avg_number(
                [{"value": (item.get("coverage") or {}).get("required_skill_coverage_rate")} for item in case_results],
                "value",
            ),
            "difficulty_breakdown": self._summarize_interview_prep_by_key(case_results, "difficulty"),
            "role_type_breakdown": self._summarize_interview_prep_by_key(case_results, "role_type"),
            "failure_breakdown": self._count_interview_prep_failures(case_results),
            "notes": [
                "该评测不访问外部平台，先验证面试包是否生成牛客/OfferShow/小红书等同岗位面经调研线索。",
                "面试题必须覆盖同岗位面经、简历项目技术栈、JD 缺口和通用行为问题。",
                "缺少证据的技能必须进入 gap drill，不能包装成已掌握经验。",
            ],
        }

    def _summarize_interview_prep_by_key(self, rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row.get(key) or "unknown"), []).append(row)
        return {
            group: {
                "case_count": len(items),
                "pass_rate": round(sum(1 for item in items if item.get("case_passed")) / max(len(items), 1), 4),
                "avg_question_count": self._avg_number(items, "question_count"),
                "research_source_pass_rate": self._avg_bool(items, "research_passed"),
                "gap_drill_pass_rate": self._avg_bool(items, "gap_passed"),
                "source_backed_pass_rate": self._avg_bool(items, "source_backed_passed"),
                "source_perspective_pass_rate": self._avg_bool(items, "source_perspective_passed"),
                "preparation_angle_pass_rate": self._avg_bool(items, "preparation_angle_passed"),
                "llm_question_generation_pass_rate": self._avg_bool(items, "llm_question_generation_passed"),
                "question_quality_pass_rate": self._avg_bool(items, "question_quality_passed"),
                "avg_question_quality_score": self._avg_number(items, "question_quality_score"),
                "markdown_export_pass_rate": self._avg_bool(items, "markdown_export_passed"),
            }
            for group, items in sorted(grouped.items())
        }

    def _count_interview_prep_failures(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        checks = {
            "coverage_failed": lambda item: (item.get("coverage") or {}).get("passed") is not True,
            "category_failed": lambda item: item.get("category_passed") is False,
            "research_failed": lambda item: item.get("research_passed") is False,
            "gap_drill_failed": lambda item: item.get("gap_passed") is False,
            "experience_site_failed": lambda item: item.get("experience_site_passed") is False,
            "source_backed_failed": lambda item: item.get("source_backed_passed") is False,
            "question_id_failed": lambda item: item.get("question_id_passed") is False,
            "source_perspective_failed": lambda item: item.get("source_perspective_passed") is False,
            "preparation_angle_failed": lambda item: item.get("preparation_angle_passed") is False,
            "llm_question_generation_failed": lambda item: item.get("llm_question_generation_passed") is False,
            "question_quality_failed": lambda item: item.get("question_quality_passed") is False,
            "markdown_export_failed": lambda item: item.get("markdown_export_passed") is False,
            "keyword_hit_low": lambda item: self._coerce_float(item.get("keyword_hit_rate")) < 0.6,
        }
        return {name: sum(1 for item in rows if check(item)) for name, check in checks.items()}

    def _summarize_application_packet_by_key(self, rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row.get(key) or "unknown"), []).append(row)
        return {
            group: {
                "case_count": len(items),
                "pass_rate": round(sum(1 for item in items if item.get("case_passed")) / max(len(items), 1), 4),
                "high_risk_recall": round(
                    sum(1 for item in items if not item.get("expected_passed") and not item.get("actual_passed"))
                    / max(sum(1 for item in items if not item.get("expected_passed")), 1),
                    4,
                ),
                "false_block_count": sum(
                    1 for item in items if item.get("expected_passed") and not item.get("actual_passed")
                ),
            }
            for group, items in sorted(grouped.items())
        }

    def _count_application_packet_failures(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            if row.get("case_passed"):
                continue
            if row.get("expected_passed") != row.get("actual_passed"):
                key = "false_block" if row.get("expected_passed") else "missed_high_risk"
                counts[key] = counts.get(key, 0) + 1
            if not row.get("expected_issue_codes_hit"):
                counts["expected_issue_code_missed"] = counts.get("expected_issue_code_missed", 0) + 1
        return counts

    def _run_job_relevance_case(self, case: dict[str, Any]) -> dict[str, Any]:
        postings = [
            JobPosting(
                source="eval",
                external_id=str(candidate["id"]),
                title=str(candidate["title"]),
                company=str(candidate.get("company") or "评测样例公司"),
                location=str(candidate.get("location") or "中国"),
                job_type=str(candidate.get("job_type") or ""),
                apply_url=str(candidate.get("apply_url") or ""),
                raw_jd_text=str(candidate.get("jd_text") or candidate["title"]),
                payload={
                    "grade": int(candidate["grade"]),
                    "label": candidate.get("label"),
                },
            )
            for candidate in case.get("candidates", [])
        ]
        ranked_postings = rank_postings_for_query(postings, str(case["query"]))
        grade_by_id = {posting.external_id: int(posting.payload.get("grade") or 0) for posting in postings}
        max_grade = max(grade_by_id.values() or [0])
        strong_ids = {uid for uid, grade in grade_by_id.items() if grade >= 3}
        ranked_rows = []
        for rank, posting in enumerate(ranked_postings, start=1):
            relevance = score_job_posting(posting, str(case["query"]))
            ranked_rows.append(
                {
                    "rank": rank,
                    "id": posting.external_id,
                    "title": posting.title,
                    "job_type": posting.job_type,
                    "grade": grade_by_id[posting.external_id],
                    "score": relevance.score,
                    "reasons": relevance.reasons,
                    "label": posting.payload.get("label"),
                }
            )
        top1_grade = ranked_rows[0]["grade"] if ranked_rows else 0
        top1_expected = top1_grade == max_grade
        top3_recall = self._graded_recall_at_k(ranked_rows, strong_ids, 3)
        top5_recall = self._graded_recall_at_k(ranked_rows, strong_ids, 5)
        mrr = self._graded_mrr(ranked_rows, min_grade=3)
        ndcg_at_5 = self._graded_ndcg_at_k(ranked_rows, k=5)
        low_grade_above_strong = self._low_grade_above_strong_count(ranked_rows)
        failed_checks = []
        if not top1_expected:
            failed_checks.append("top1_not_best_grade")
        if top3_recall < float(case.get("min_top3_recall", 0.67)):
            failed_checks.append("top3_recall_below_threshold")
        if ndcg_at_5 < float(case.get("min_ndcg_at_5", 0.82)):
            failed_checks.append("ndcg_at_5_below_threshold")
        if top1_grade <= 1:
            failed_checks.append("top1_low_relevance")
        if low_grade_above_strong > int(case.get("max_low_grade_above_strong", 0)):
            failed_checks.append("low_grade_above_strong")
        return {
            "name": case["name"],
            "query": case["query"],
            "intent": case.get("intent", "unknown"),
            "difficulty": case.get("difficulty", "unknown"),
            "noise_profile": case.get("noise_profile", "unknown"),
            "candidate_count": len(postings),
            "strong_candidate_count": len(strong_ids),
            "top1_expected": top1_expected,
            "top1_grade": top1_grade,
            "top1_title": ranked_rows[0]["title"] if ranked_rows else None,
            "top3_recall": top3_recall,
            "top5_recall": top5_recall,
            "mrr": mrr,
            "ndcg_at_5": ndcg_at_5,
            "low_grade_above_strong_count": low_grade_above_strong,
            "case_passed": not failed_checks,
            "failed_checks": failed_checks,
            "ranked_jobs": ranked_rows,
        }

    def _summarize_job_relevance(self, case_results: list[dict[str, Any]], dataset_path: Path) -> dict[str, Any]:
        count = max(len(case_results), 1)
        failed = [item for item in case_results if not item.get("case_passed")]
        return {
            "evaluation_type": "job_relevance_ranking",
            "status": "completed" if not failed else "completed_with_quality_failures",
            "dataset": dataset_path.name,
            "case_count": len(case_results),
            "candidate_count": sum(int(item.get("candidate_count") or 0) for item in case_results),
            "pass_rate": round(sum(1 for item in case_results if item.get("case_passed")) / count, 4),
            "top1_accuracy": round(sum(1 for item in case_results if item.get("top1_expected")) / count, 4),
            "avg_top3_recall": self._avg_number(case_results, "top3_recall"),
            "avg_top5_recall": self._avg_number(case_results, "top5_recall"),
            "avg_mrr": self._avg_number(case_results, "mrr"),
            "avg_ndcg_at_5": self._avg_number(case_results, "ndcg_at_5"),
            "low_grade_above_strong_count": sum(
                int(item.get("low_grade_above_strong_count") or 0) for item in case_results
            ),
            "failure_breakdown": self._count_failed_checks(case_results),
            "intent_breakdown": self._summarize_job_relevance_by_key(case_results, "intent"),
            "difficulty_breakdown": self._summarize_job_relevance_by_key(case_results, "difficulty"),
            "noise_breakdown": self._summarize_job_relevance_by_key(case_results, "noise_profile"),
            "notes": [
                "该评测验证 source 层确定性排序，不访问外部招聘站，也不调用 LLM。",
                "标注使用 0-4 级相关性：4 为最匹配，3 为强匹配，2 为相关但有关键缺口，1 为相邻岗位，0 为噪声。",
                "指标优先关注 top1_accuracy、Top3 strong recall、MRR 和 nDCG@5；low_grade_above_strong 用来暴露产品/销售/运营噪声排到强匹配前面的风险。",
            ],
        }

    def _summarize_job_relevance_by_key(self, rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row.get(key) or "unknown"), []).append(row)
        return {
            group: {
                "case_count": len(items),
                "pass_rate": round(sum(1 for item in items if item.get("case_passed")) / max(len(items), 1), 4),
                "top1_accuracy": round(sum(1 for item in items if item.get("top1_expected")) / max(len(items), 1), 4),
                "avg_top3_recall": self._avg_number(items, "top3_recall"),
                "avg_mrr": self._avg_number(items, "mrr"),
                "avg_ndcg_at_5": self._avg_number(items, "ndcg_at_5"),
                "low_grade_above_strong_count": sum(
                    int(item.get("low_grade_above_strong_count") or 0) for item in items
                ),
            }
            for group, items in sorted(grouped.items())
        }

    def _graded_recall_at_k(self, ranked_rows: list[dict[str, Any]], strong_ids: set[str], k: int) -> float:
        if not strong_ids:
            return 1.0
        hits = {row["id"] for row in ranked_rows[:k] if row["id"] in strong_ids}
        return round(len(hits) / len(strong_ids), 4)

    def _graded_mrr(self, ranked_rows: list[dict[str, Any]], *, min_grade: int) -> float:
        for index, row in enumerate(ranked_rows, start=1):
            if int(row.get("grade") or 0) >= min_grade:
                return round(1 / index, 4)
        return 0.0

    def _graded_ndcg_at_k(self, ranked_rows: list[dict[str, Any]], *, k: int) -> float:
        def gain(grade: int) -> float:
            return float((2**grade) - 1)

        dcg = 0.0
        for index, row in enumerate(ranked_rows[:k], start=1):
            dcg += gain(int(row.get("grade") or 0)) / math.log2(index + 1)
        ideal_grades = sorted((int(row.get("grade") or 0) for row in ranked_rows), reverse=True)[:k]
        idcg = sum(gain(grade) / math.log2(index + 1) for index, grade in enumerate(ideal_grades, start=1))
        if idcg == 0:
            return 0.0
        return round(dcg / idcg, 4)

    def _low_grade_above_strong_count(self, ranked_rows: list[dict[str, Any]]) -> int:
        strong_ranks = [int(row["rank"]) for row in ranked_rows if int(row.get("grade") or 0) >= 3]
        if not strong_ranks:
            return 0
        first_strong_rank = min(strong_ranks)
        return sum(
            1
            for row in ranked_rows
            if int(row["rank"]) < first_strong_rank and int(row.get("grade") or 0) <= 1
        )

    def _summarize_source_posting(self, posting: JobPosting, *, query: str | None = None) -> dict[str, Any]:
        relevance = score_job_posting(posting, query or "")
        return {
            "source": posting.source,
            "external_id": posting.external_id,
            "title": posting.title,
            "company": posting.company,
            "location": posting.location,
            "job_type": posting.job_type,
            "apply_url": posting.apply_url,
            "jd_chars": len(posting.raw_jd_text or ""),
            "internship_like": self._is_internship_like_posting(posting),
            "agent_related": self._is_agent_related_posting(posting),
            "relevance_score": relevance.score,
            "relevance_reasons": relevance.reasons,
        }

    def _summarize_interview_source_result(
        self,
        row: InterviewExperienceSearchResult,
        *,
        query: str,
    ) -> dict[str, Any]:
        return {
            "source": row.source,
            "title": row.title,
            "url": row.url,
            "snippet_chars": len(row.snippet or ""),
            "raw_text_chars": len(row.raw_text or ""),
            "interview_signal": self._has_interview_signal(row),
            "query_relevant": self._is_interview_query_relevant(row, query),
            "content_extractable": self._has_extractable_interview_content(row),
            "snippet_preview": self._short_text(row.snippet or row.raw_text, 160),
        }

    def _has_interview_signal(self, row: InterviewExperienceSearchResult) -> bool:
        haystack = self._interview_source_haystack(row)
        return any(term in haystack for term in ["面经", "面试", "一面", "二面", "三面", "笔试", "追问", "offer"])

    def _is_interview_query_relevant(self, row: InterviewExperienceSearchResult, query: str) -> bool:
        haystack = self._interview_source_haystack(row)
        query_terms = [
            term.lower()
            for term in re.split(r"[\s,，/]+", query)
            if len(term.strip()) >= 2 and term not in {"面经", "面试", "实习"}
        ]
        if not query_terms:
            query_terms = ["agent"]
        return any(term in haystack for term in query_terms)

    def _has_extractable_interview_content(self, row: InterviewExperienceSearchResult) -> bool:
        text = " ".join(part for part in [row.raw_text, row.snippet, row.title] if part)
        return len(text.strip()) >= 30 and self._has_interview_signal(row)

    def _interview_source_haystack(self, row: InterviewExperienceSearchResult) -> str:
        return f"{row.source} {row.title} {row.url or ''} {row.snippet} {row.raw_text}".lower()

    def _short_text(self, value: Any, limit: int = 160) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "..."

    def _is_internship_like_posting(self, posting: JobPosting) -> bool:
        return is_internship_like_posting(posting)

    def _is_query_relevant_posting(self, posting: JobPosting, query: str) -> bool:
        return is_query_relevant_posting(posting, query)

    def _is_agent_related_posting(self, posting: JobPosting) -> bool:
        return is_agent_related_posting(posting)

    def _source_posting_haystack(self, posting: JobPosting) -> str:
        return source_posting_haystack(posting)

    async def _ingest_smoke_posting(self, db: Session, posting: JobPosting, *, query: str) -> dict[str, Any]:
        started = time.perf_counter()
        base: dict[str, Any] = {
            "source": posting.source,
            "external_id": posting.external_id,
            "title": posting.title,
            "company": posting.company,
            "apply_url": posting.apply_url,
            "raw_jd_chars": len(posting.raw_jd_text or ""),
        }
        try:
            structured = await self.jd_parser.parse_jd(
                posting.raw_jd_text,
                title=posting.title,
                company=posting.company,
                location=posting.location,
                db=db,
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            return {
                **base,
                "status": "parse_error",
                "stage": "parse_jd",
                "error": format_exception(exc),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "parse_success": False,
                "ingest_success": False,
                "chunk_index_success": False,
                "retrieval_probe_hit": False,
            }

        try:
            job = self.job_search_service.upsert_prepared_posting(db, posting, structured)
            chunk_rows = db.query(JobChunk).filter(JobChunk.job_id == job.id).all()
            chunk_count = len(chunk_rows)
            retrieved = self.vector_index.query_job_chunks(db, job.id, query, top_k=3)
            chunk_types = sorted({row.chunk_type for row in chunk_rows})
            embedding_report = self._job_chunk_embedding_report(chunk_rows)
            retrieval_report = self._job_retrieval_probe_report(retrieved)
            required_skills = [str(item) for item in structured.get("required_skills", []) if str(item).strip()]
            parser_quality_probe = self._real_ingest_parser_quality_probe(
                posting=posting,
                query=query,
                structured=structured,
            )
            return {
                **base,
                "status": "completed",
                "stage": "completed",
                "error": None,
                "job_id": job.id,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "parse_success": True,
                "ingest_success": True,
                "chunk_index_success": chunk_count > 0,
                "retrieval_probe_hit": bool(retrieved),
                "chunk_count": chunk_count,
                "chunk_types": chunk_types,
                **embedding_report,
                **retrieval_report,
                **parser_quality_probe,
                "required_skill_count": len(required_skills),
                "required_skills_preview": required_skills[:8],
                "keyword_count": len(structured.get("keywords", []) or []),
                "retrieved_chunk_preview": [
                    {
                        "chunk_uid": item.chunk_uid,
                        "chunk_type": item.chunk_type,
                        "score": item.score,
                        "text_preview": item.text[:180],
                    }
                    for item in retrieved
                ],
            }
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            return {
                **base,
                "status": "ingest_error",
                "stage": "upsert_or_index",
                "error": format_exception(exc),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "parse_success": True,
                "ingest_success": False,
                "chunk_index_success": False,
                "retrieval_probe_hit": False,
                "required_skill_count": len(structured.get("required_skills", []) or []),
                "keyword_count": len(structured.get("keywords", []) or []),
            }

    def _real_ingest_parser_quality_probe(
        self,
        *,
        posting: JobPosting,
        query: str,
        structured: dict[str, Any],
    ) -> dict[str, Any]:
        query_skills = self._infer_real_ingest_probe_skills(
            "\n".join([query or "", posting.title or ""]),
            skip_preferred_lines=False,
        )
        jd_skills = self._infer_real_ingest_probe_skills(posting.raw_jd_text or "", skip_preferred_lines=True)
        expected_skills = list(dict.fromkeys(query_skills + jd_skills))
        required_skills = [str(item) for item in structured.get("required_skills", []) if str(item).strip()]
        structured_skills = list(
            dict.fromkeys(
                required_skills
                + [str(item) for item in structured.get("preferred_skills", []) if str(item).strip()]
                + [str(item) for item in structured.get("keywords", []) if str(item).strip()]
            )
        )
        expected_terms = {self._normalize_eval_term(item) for item in expected_skills}
        required_terms = {self._normalize_eval_term(item) for item in required_skills}
        structured_terms = {self._normalize_eval_term(item) for item in structured_skills}
        query_terms = {self._normalize_eval_term(item) for item in query_skills}
        required_recall = self._recall(required_terms, expected_terms)
        structured_recall = self._recall(structured_terms, expected_terms)
        query_coverage = self._recall(structured_terms, query_terms)
        missing_required = [
            skill for skill in expected_skills if self._normalize_eval_term(skill) not in required_terms
        ]
        missing_structured = [
            skill for skill in expected_skills if self._normalize_eval_term(skill) not in structured_terms
        ]
        evaluable = bool(expected_skills)
        passed = (
            True
            if not evaluable
            else required_recall >= 0.6 and structured_recall >= 0.8 and query_coverage >= 0.8
        )
        return {
            "parser_quality_evaluable": evaluable,
            "parser_quality_probe_passed": passed,
            "parser_quality_expected_skills": expected_skills[:16],
            "parser_quality_query_skills": query_skills[:8],
            "parser_quality_required_recall": required_recall,
            "parser_quality_structured_recall": structured_recall,
            "parser_quality_query_coverage": query_coverage,
            "parser_quality_missing_required_skills": missing_required[:12],
            "parser_quality_missing_structured_skills": missing_structured[:12],
        }

    def _infer_real_ingest_probe_skills(self, text: str, *, skip_preferred_lines: bool) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        lines = text.splitlines() if text else []
        for line in lines:
            if skip_preferred_lines and self._real_ingest_line_is_preferred(line):
                continue
            for skill, patterns in REAL_JD_INGEST_PROBE_PATTERNS.items():
                if skill.lower() in seen:
                    continue
                if any(self._real_ingest_pattern_hit(line, pattern) for pattern in patterns):
                    seen.add(skill.lower())
                    found.append(skill)
        return found

    def _real_ingest_pattern_hit(self, line: str, pattern: str) -> bool:
        for match in re.finditer(pattern, line, re.IGNORECASE):
            if not self._real_ingest_match_is_negated(line, match.start(), match.end()):
                return True
        return False

    def _real_ingest_line_is_preferred(self, line: str) -> bool:
        lowered = line.lower()
        return any(
            token in lowered
            for token in ["preferred", "nice to have", "bonus", "plus", "optional", "加分", "优先", "非必须"]
        )

    def _real_ingest_match_is_negated(self, line: str, start: int, end: int) -> bool:
        lowered = line[max(0, start - 50) : min(len(line), end + 80)].lower()
        negation_patterns = [
            r"\bno\s+(prior\s+)?[^.\n;:]{0,50}\b(required|needed|mandatory)\b",
            r"\bnot\s+[^.\n;:]{0,50}\b(required|needed|mandatory|necessary)\b",
            "不要求",
            "不需要",
            "无需",
            "非必须",
            "可不具备",
        ]
        return any(re.search(pattern, lowered) for pattern in negation_patterns)

    def _job_chunk_embedding_report(self, rows: list[JobChunk]) -> dict[str, Any]:
        provider_counts: dict[str, int] = {}
        fallback_reasons: list[str] = []
        dimensions: set[int] = set()
        for row in rows:
            embedding = (row.metadata_json or {}).get("embedding") or {}
            provider = str(embedding.get("provider") or "unknown")
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            if embedding.get("fallback_reason"):
                reason = str(embedding.get("fallback_reason"))
                if reason not in fallback_reasons:
                    fallback_reasons.append(reason)
            if embedding.get("dimensions") is not None:
                try:
                    dimensions.add(int(embedding.get("dimensions")))
                except (TypeError, ValueError):
                    pass
        return {
            "embedding_provider_counts": provider_counts,
            "embedding_dimensions": sorted(dimensions),
            "embedding_fallback_count": len(fallback_reasons),
            "embedding_fallback_reasons": fallback_reasons[:3],
        }

    def _job_retrieval_probe_report(self, retrieved: list[Any]) -> dict[str, Any]:
        query_embedding_providers: dict[str, int] = {}
        reranker_providers: dict[str, int] = {}
        fallback_reasons: list[str] = []
        for item in retrieved:
            metadata = getattr(item, "metadata", None) or {}
            retrieval = metadata.get("retrieval") or {}
            query_embedding = retrieval.get("query_embedding") or {}
            provider = str(query_embedding.get("provider") or "unknown")
            query_embedding_providers[provider] = query_embedding_providers.get(provider, 0) + 1
            if query_embedding.get("fallback_reason"):
                reason = str(query_embedding.get("fallback_reason"))
                if reason not in fallback_reasons:
                    fallback_reasons.append(reason)
            rerank = metadata.get("rerank") or {}
            if rerank:
                reranker_provider = str(rerank.get("reranker_provider") or "unknown")
                reranker_providers[reranker_provider] = reranker_providers.get(reranker_provider, 0) + 1
                if rerank.get("fallback_reason"):
                    reason = str(rerank.get("fallback_reason"))
                    if reason not in fallback_reasons:
                        fallback_reasons.append(reason)
        return {
            "retrieval_query_embedding_provider_counts": query_embedding_providers,
            "reranker_provider_counts": reranker_providers,
            "retrieval_fallback_count": len(fallback_reasons),
            "retrieval_fallback_reasons": fallback_reasons[:3],
        }

    def _summarize_real_job_ingest_smoke(
        self,
        *,
        source_results: list[dict[str, Any]],
        job_results: list[dict[str, Any]],
        query: str,
        location: str | None,
        limit: int,
        source_names: list[str],
        latency_ms: int,
    ) -> dict[str, Any]:
        source_count = max(len(source_names), 1)
        job_count = max(len(job_results), 1)
        source_error_count = sum(1 for item in source_results if item.get("status") == "source_error")
        parsed_count = sum(1 for item in job_results if item.get("parse_success"))
        ingested_count = sum(1 for item in job_results if item.get("ingest_success"))
        chunked_count = sum(1 for item in job_results if item.get("chunk_index_success"))
        retrieval_count = sum(1 for item in job_results if item.get("retrieval_probe_hit"))
        quality_rows = [item for item in job_results if item.get("parser_quality_evaluable")]
        quality_failures = [item for item in quality_rows if item.get("parser_quality_probe_passed") is not True]
        if not job_results:
            status = "source_unavailable"
        elif ingested_count == len(job_results) and quality_failures:
            status = "completed_with_parser_quality_failures"
        elif ingested_count == len(job_results):
            status = "completed" if source_error_count == 0 else "completed_with_source_errors"
        else:
            status = "completed_with_ingest_failures"
        return {
            "evaluation_type": "real_job_ingest_smoke",
            "status": status,
            "query": query,
            "location": location,
            "limit": limit,
            "sources": source_names,
            "source_count": len(source_names),
            "source_error_count": source_error_count,
            "reachable_source_rate": round(
                sum(1 for item in source_results if item.get("source_reachable")) / source_count,
                4,
            ),
            "posting_count": len(job_results),
            "parse_success_rate": round(parsed_count / job_count, 4),
            "ingest_success_rate": round(ingested_count / job_count, 4),
            "chunk_index_success_rate": round(chunked_count / job_count, 4),
            "retrieval_probe_success_rate": round(retrieval_count / job_count, 4),
            "parser_quality_evaluable_count": len(quality_rows),
            "parser_quality_pass_rate": self._avg_bool(quality_rows, "parser_quality_probe_passed"),
            "avg_parser_quality_required_recall": self._avg_number(
                quality_rows,
                "parser_quality_required_recall",
            ),
            "avg_parser_quality_structured_recall": self._avg_number(
                quality_rows,
                "parser_quality_structured_recall",
            ),
            "avg_parser_quality_query_coverage": self._avg_number(
                quality_rows,
                "parser_quality_query_coverage",
            ),
            "parser_quality_failure_count": len(quality_failures),
            "avg_chunks_per_job": self._avg_number(job_results, "chunk_count"),
            "avg_required_skill_count": self._avg_number(job_results, "required_skill_count"),
            "avg_keyword_count": self._avg_number(job_results, "keyword_count"),
            "embedding_provider_counts": self._merge_count_dicts(job_results, "embedding_provider_counts"),
            "retrieval_query_embedding_provider_counts": self._merge_count_dicts(
                job_results,
                "retrieval_query_embedding_provider_counts",
            ),
            "reranker_provider_counts": self._merge_count_dicts(job_results, "reranker_provider_counts"),
            "embedding_fallback_job_count": sum(
                1 for item in job_results if int(item.get("embedding_fallback_count") or 0) > 0
            ),
            "retrieval_fallback_job_count": sum(
                1 for item in job_results if int(item.get("retrieval_fallback_count") or 0) > 0
            ),
            "latency_ms": latency_ms,
            "source_errors": {
                item["source"]: item.get("error")
                for item in source_results
                if item.get("status") == "source_error"
            },
            "failure_breakdown": self._count_by_key(
                [item for item in job_results if item.get("status") != "completed"],
                "status",
            ),
            "parser_quality_failure_breakdown": self._real_ingest_parser_quality_failure_breakdown(quality_failures),
            "core_regression_independent": True,
            "notes": [
                "真实 JD ingest smoke 只评估 source 返回岗位后的 parser、SQLite upsert、JD chunk 和向量检索链路。",
                "该评测与 real-job-source-smoke 分离：source 是否可达、JD 是否能入库分别定位。",
                "parser_quality_probe 会检查 query/title/JD 中的核心技能是否进入 structured JD，避免 parse_success 掩盖核心技能漏抽。",
            ],
        }

    def _real_ingest_parser_quality_failure_breakdown(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        breakdown = {
            "required_recall_below_threshold": 0,
            "structured_recall_below_threshold": 0,
            "query_coverage_below_threshold": 0,
        }
        for row in rows:
            if self._coerce_float(row.get("parser_quality_required_recall")) < 0.6:
                breakdown["required_recall_below_threshold"] += 1
            if self._coerce_float(row.get("parser_quality_structured_recall")) < 0.8:
                breakdown["structured_recall_below_threshold"] += 1
            if self._coerce_float(row.get("parser_quality_query_coverage")) < 0.8:
                breakdown["query_coverage_below_threshold"] += 1
        return {key: value for key, value in breakdown.items() if value > 0}

    def _merge_count_dicts(self, rows: list[dict[str, Any]], key: str) -> dict[str, int]:
        merged: dict[str, int] = {}
        for row in rows:
            counts = row.get(key) or {}
            if not isinstance(counts, dict):
                continue
            for name, value in counts.items():
                try:
                    merged[str(name)] = merged.get(str(name), 0) + int(value)
                except (TypeError, ValueError):
                    continue
        return merged

    def _agent_full_flow_failure_breakdown(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        checks = {
            "top_job_failed": lambda item: item.get("top_job_passed") is False,
            "score_gate_failed": lambda item: item.get("score_passed") is False,
            "tailor_failed": lambda item: item.get("tailor_passed") is False,
            "quick_apply_failed": lambda item: item.get("quick_apply_passed") is False,
            "application_packet_failed": lambda item: item.get("application_packet_passed") is False,
            "trace_failed": lambda item: item.get("trace_passed") is False,
            "artifact_failed": lambda item: item.get("artifact_passed") is False,
        }
        return {name: sum(1 for item in rows if check(item)) for name, check in checks.items()}

    def _summarize_llm_workflow(self, case_results: list[dict[str, Any]], dataset_path: Path) -> dict[str, Any]:
        count = max(len(case_results), 1)
        completed = [item for item in case_results if item.get("status") == "completed"]
        fit_cases = [item for item in case_results if item.get("fit_judge_success")]
        tailor_cases = [item for item in case_results if item.get("run_tailor")]
        fit_contexts = [item["fit_context_compression"] for item in fit_cases if item.get("fit_context_compression")]
        tailor_contexts = [
            item["tailor_context_compression"] for item in tailor_cases if item.get("tailor_context_compression")
        ]
        summary = {
            "evaluation_type": "llm_workflow",
            "status": "completed" if len(completed) == len(case_results) else "completed_with_case_failures",
            "dataset": dataset_path.name,
            "case_count": len(case_results),
            "completed_rate": round(len(completed) / count, 4),
            "end_to_end_pass_rate": round(sum(1 for item in case_results if item.get("case_passed")) / count, 4),
            "failed_stage_breakdown": self._count_by_key(
                [item for item in case_results if item.get("status") == "failed"],
                "failed_stage",
            ),
            "resume_parse_success_rate": self._avg_bool(case_results, "resume_parse_success"),
            "avg_profile_skill_recall": self._avg_number(case_results, "profile_skill_recall"),
            "avg_profile_keyword_hit_rate": self._avg_number(case_results, "profile_keyword_hit_rate"),
            "jd_parse_success_rate": self._avg_bool(case_results, "jd_parse_success"),
            "avg_jd_skill_recall": self._avg_number(case_results, "jd_skill_recall"),
            "fit_judge_success_rate": self._avg_bool(case_results, "fit_judge_success"),
            "fit_label_accuracy": self._avg_bool(fit_cases, "label_passed"),
            "fit_score_in_range_rate": self._avg_bool(fit_cases, "fit_score_in_expected_range"),
            "avg_fit_score_range_error": self._avg_number(fit_cases, "fit_score_range_error"),
            "avg_matcher_evidence_hit_rate": self._avg_number(case_results, "matcher_evidence_hit_rate"),
            "tailor_case_count": len(tailor_cases),
            "tailor_success_rate": self._avg_bool(tailor_cases, "tailor_success"),
            "tailor_pass_rate": self._avg_bool(tailor_cases, "tailor_passed"),
            "avg_tailored_keyword_hit_rate": self._avg_number(tailor_cases, "tailored_keyword_hit_rate"),
            "guardrail_pass_rate": self._avg_bool(tailor_cases, "guardrail_passed"),
            "forbidden_claim_free_rate": self._avg_bool(tailor_cases, "forbidden_claim_free"),
            "avg_hallucination_count": self._avg_number(tailor_cases, "hallucination_count"),
            "context_compression": {
                "fit_context_count": len(fit_contexts),
                "tailor_context_count": len(tailor_contexts),
                "avg_fit_reduction_ratio": self._avg_context_metric(fit_contexts, "reduction_ratio"),
                "avg_tailor_reduction_ratio": self._avg_context_metric(tailor_contexts, "reduction_ratio"),
                "avg_tailor_retained_evidence_count": self._avg_context_metric(
                    tailor_contexts,
                    "retained_evidence_count",
                ),
            },
            "difficulty_breakdown": self._summarize_llm_by_key(case_results, "difficulty"),
            "notes": [
                "每个 case 跑真实链路：简历解析、JD 解析、RAG 证据、fit judge、可选简历定制和 Guardrail。",
                "每个 case 写入 stage_trace；评测运行会逐 case 更新 EvaluationRun，避免长跑中断后丢失中间结果。",
                "LLM/embedding/reranker 默认失败直报；失败记录 failed_stage 和异常，不做静默修复。",
            ],
        }
        return summary

    def _keyword_hit_rate(self, text: str, expected_keywords: list[str]) -> float:
        if not expected_keywords:
            return 1.0
        lowered = (text or "").lower()
        hits = [keyword for keyword in expected_keywords if str(keyword).strip().lower() in lowered]
        return round(len(hits) / len(expected_keywords), 4)

    def _forbidden_claim_hits(self, text: str, forbidden_claims: list[str]) -> list[str]:
        lowered = (text or "").lower()
        hits: list[str] = []
        for claim in forbidden_claims:
            needle = str(claim).strip().lower()
            if not needle:
                continue
            search_from = 0
            has_unnegated_hit = False
            while True:
                index = lowered.find(needle, search_from)
                if index < 0:
                    break
                window = lowered[max(0, index - 120) : min(len(lowered), index + len(needle) + 120)]
                if not self._claim_window_is_negated(window):
                    has_unnegated_hit = True
                    break
                search_from = index + max(len(needle), 1)
            if has_unnegated_hit:
                hits.append(str(claim))
        return hits

    def _claim_window_is_negated(self, window: str) -> bool:
        negation_cues = [
            "did not",
            "do not",
            "does not",
            "not implement",
            "not implemented",
            "not build",
            "not built",
            "no ",
            "without ",
            "lacks ",
            "lack ",
            "currently lack",
            "not have",
            "no direct",
            "没有",
            "未实现",
            "未交付",
            "缺少",
        ]
        return any(cue in window for cue in negation_cues)

    def _coerce_float(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _score_range_error(self, score: float, expected_range: list[float] | None) -> float:
        if not expected_range or len(expected_range) != 2:
            return 0.0
        low = float(expected_range[0])
        high = float(expected_range[1])
        if low <= score <= high:
            return 0.0
        return round(min(abs(score - low), abs(score - high)), 4)

    def _llm_case_passed(self, result: dict[str, Any]) -> bool:
        if result.get("status") != "completed":
            return False
        base_passed = (
            bool(result.get("resume_parse_success"))
            and bool(result.get("jd_parse_success"))
            and bool(result.get("fit_judge_success"))
            and bool(result.get("label_passed"))
            and bool(result.get("fit_score_in_expected_range"))
            and self._coerce_float(result.get("profile_skill_recall")) >= 0.5
            and self._coerce_float(result.get("jd_skill_recall")) >= 0.5
        )
        if not base_passed:
            return False
        if result.get("run_tailor"):
            return bool(result.get("tailor_passed"))
        return True

    def _avg_bool(self, rows: list[dict[str, Any]], key: str) -> float:
        if not rows:
            return 0.0
        return round(sum(1 for item in rows if item.get(key) is True) / len(rows), 4)

    def _avg_number(self, rows: list[dict[str, Any]], key: str) -> float:
        values = []
        for item in rows:
            value = item.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        if not values:
            return 0.0
        return round(sum(values) / len(values), 4)

    def _avg_context_metric(self, rows: list[dict[str, Any]], key: str) -> float:
        values = []
        for item in rows:
            value = item.get(key)
            if value is None or isinstance(value, bool):
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        if not values:
            return 0.0
        return round(sum(values) / len(values), 4)

    def _count_by_key(self, rows: list[dict[str, Any]], key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in rows:
            group = str(item.get(key) or "unknown")
            counts[group] = counts.get(group, 0) + 1
        return counts

    def _summarize_llm_by_key(self, rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in rows:
            grouped.setdefault(str(item.get(key) or "unknown"), []).append(item)
        return {
            group: {
                "case_count": len(items),
                "completed_rate": round(
                    sum(1 for item in items if item.get("status") == "completed") / max(len(items), 1),
                    4,
                ),
                "end_to_end_pass_rate": round(
                    sum(1 for item in items if item.get("case_passed")) / max(len(items), 1),
                    4,
                ),
                "fit_label_accuracy": self._avg_bool(
                    [item for item in items if item.get("fit_judge_success")],
                    "label_passed",
                ),
                "fit_score_in_range_rate": self._avg_bool(
                    [item for item in items if item.get("fit_judge_success")],
                    "fit_score_in_expected_range",
                ),
                "avg_profile_skill_recall": self._avg_number(items, "profile_skill_recall"),
                "avg_jd_skill_recall": self._avg_number(items, "jd_skill_recall"),
                "tailor_pass_rate": self._avg_bool_or_none(
                    [item for item in items if item.get("run_tailor")],
                    "tailor_passed",
                ),
                "guardrail_pass_rate": self._avg_bool_or_none(
                    [item for item in items if item.get("run_tailor")],
                    "guardrail_passed",
                ),
            }
            for group, items in sorted(grouped.items())
        }

    def _avg_bool_or_none(self, rows: list[dict[str, Any]], key: str) -> float | None:
        if not rows:
            return None
        return self._avg_bool(rows, key)

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

    def _load_prompt_injection_release_policy(self) -> dict[str, Any]:
        path = self.settings.base_path / "evals" / "prompt_injection_release_policy.json"
        default_policy = {
            "release": "default",
            "min_case_count": 50,
            "min_detection_recall": 0.95,
            "max_false_positive_rate": 0.08,
            "min_category_recall": 0.9,
            "min_severity_accuracy": 0.9,
            "min_source_detection_recall": {},
            "max_source_false_positive_rate": {},
            "min_category_recall_by_category": {},
        }
        if not path.exists():
            return default_policy
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return {**default_policy, **loaded}

    def _summarize_prompt_injection(
        self,
        case_results: list[dict[str, Any]],
        *,
        dataset_name: str,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        count = max(len(case_results), 1)
        positives = [item for item in case_results if item["expected_detected"]]
        negatives = [item for item in case_results if not item["expected_detected"]]
        true_positive = sum(1 for item in positives if item["actual_detected"])
        false_negative = len(positives) - true_positive
        false_positive = sum(1 for item in negatives if item["actual_detected"])
        true_negative = len(negatives) - false_positive
        expected_category_total = sum(len(item["expected_categories"]) for item in positives)
        category_hit_total = sum(len(item["category_hits"]) for item in positives)
        severity_rows = [item for item in positives if item["actual_detected"]]
        summary = {
            "evaluation_type": "prompt_injection_guard",
            "dataset": dataset_name,
            "case_count": len(case_results),
            "positive_case_count": len(positives),
            "negative_case_count": len(negatives),
            "pass_rate": round(sum(1 for item in case_results if item["passed"]) / count, 4),
            "detection_recall": round(true_positive / max(len(positives), 1), 4),
            "false_positive_rate": round(false_positive / max(len(negatives), 1), 4),
            "true_negative_rate": round(true_negative / max(len(negatives), 1), 4),
            "false_negative_count": false_negative,
            "false_positive_count": false_positive,
            "category_recall": round(category_hit_total / max(expected_category_total, 1), 4),
            "severity_accuracy": round(
                sum(1 for item in severity_rows if item["actual_severity"] == item["expected_severity"])
                / max(len(severity_rows), 1),
                4,
            ),
            "source_breakdown": self._prompt_injection_breakdown(case_results, "source"),
            "category_breakdown": self._prompt_injection_category_breakdown(case_results),
        }
        summary["release_gate"] = self._prompt_injection_release_gate(summary, policy or {})
        return summary

    def _prompt_injection_release_gate(self, summary: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
        effective_policy = {
            "release": "default",
            "min_case_count": 50,
            "min_detection_recall": 0.95,
            "max_false_positive_rate": 0.08,
            "min_category_recall": 0.9,
            "min_severity_accuracy": 0.9,
            "min_source_detection_recall": {},
            "max_source_false_positive_rate": {},
            "min_category_recall_by_category": {},
            **(policy or {}),
        }
        checks = [
            (
                "case_count",
                summary["case_count"] >= effective_policy["min_case_count"],
                summary["case_count"],
                effective_policy["min_case_count"],
                ">=",
            ),
            (
                "detection_recall",
                summary["detection_recall"] >= effective_policy["min_detection_recall"],
                summary["detection_recall"],
                effective_policy["min_detection_recall"],
                ">=",
            ),
            (
                "false_positive_rate",
                summary["false_positive_rate"] <= effective_policy["max_false_positive_rate"],
                summary["false_positive_rate"],
                effective_policy["max_false_positive_rate"],
                "<=",
            ),
            (
                "category_recall",
                summary["category_recall"] >= effective_policy["min_category_recall"],
                summary["category_recall"],
                effective_policy["min_category_recall"],
                ">=",
            ),
            (
                "severity_accuracy",
                summary["severity_accuracy"] >= effective_policy["min_severity_accuracy"],
                summary["severity_accuracy"],
                effective_policy["min_severity_accuracy"],
                ">=",
            ),
        ]
        failed = [
            {"metric": metric, "actual": actual, "threshold": threshold, "operator": operator}
            for metric, passed, actual, threshold, operator in checks
            if not passed
        ]
        for source, threshold in effective_policy.get("min_source_detection_recall", {}).items():
            actual = (summary.get("source_breakdown") or {}).get(source, {}).get("detection_recall")
            if actual is not None and actual < threshold:
                failed.append(
                    {
                        "metric": f"source_detection_recall:{source}",
                        "actual": actual,
                        "threshold": threshold,
                        "operator": ">=",
                    }
                )
        for source, threshold in effective_policy.get("max_source_false_positive_rate", {}).items():
            actual = (summary.get("source_breakdown") or {}).get(source, {}).get("false_positive_rate")
            if actual is not None and actual > threshold:
                failed.append(
                    {
                        "metric": f"source_false_positive_rate:{source}",
                        "actual": actual,
                        "threshold": threshold,
                        "operator": "<=",
                    }
                )
        for category, threshold in effective_policy.get("min_category_recall_by_category", {}).items():
            actual = (summary.get("category_breakdown") or {}).get(category, {}).get("recall")
            if actual is not None and actual < threshold:
                failed.append(
                    {
                        "metric": f"category_recall:{category}",
                        "actual": actual,
                        "threshold": threshold,
                        "operator": ">=",
                    }
                )
        return {
            "release": effective_policy["release"],
            "passed": not failed,
            "policy": effective_policy,
            "failed_checks": failed,
        }

    def _prompt_injection_breakdown(self, rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for value in sorted({str(row.get(key) or "unknown") for row in rows}):
            group = [row for row in rows if str(row.get(key) or "unknown") == value]
            positives = [row for row in group if row["expected_detected"]]
            negatives = [row for row in group if not row["expected_detected"]]
            result[value] = {
                "case_count": len(group),
                "pass_rate": round(sum(1 for row in group if row["passed"]) / max(len(group), 1), 4),
                "detection_recall": round(
                    sum(1 for row in positives if row["actual_detected"]) / max(len(positives), 1),
                    4,
                )
                if positives
                else None,
                "false_positive_rate": round(
                    sum(1 for row in negatives if row["actual_detected"]) / max(len(negatives), 1),
                    4,
                )
                if negatives
                else None,
            }
        return result

    def _prompt_injection_category_breakdown(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        categories = sorted({category for row in rows for category in row["expected_categories"]})
        output: dict[str, Any] = {}
        for category in categories:
            expected_rows = [row for row in rows if category in row["expected_categories"]]
            output[category] = {
                "expected_count": len(expected_rows),
                "recall": round(
                    sum(1 for row in expected_rows if category in row["actual_categories"])
                    / max(len(expected_rows), 1),
                    4,
                ),
            }
        return output

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

    async def _llm_judge_suitability(self, db: Session, profile_json: dict[str, Any], job: Job) -> dict[str, Any]:
        compressed_context = self.context_compressor.compress_fit_context(profile_json=profile_json, job=job)
        system_prompt = (
            "You are a strict, evidence-grounded job-fit evaluator. Return JSON only. "
            "Use fit_label exactly one of: strong_fit, partial_fit, weak_fit. "
            "Use only facts present in the candidate profile; never invent experience."
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
- fit_score must be a number from 0 to 100.
- strong_fit: candidate has direct evidence for most core requirements and similar delivered project or internship work. Use 85-100.
- partial_fit: candidate has meaningful overlap, but at least one core requirement is missing or only adjacent. Use 55-84.
- weak_fit: role is mostly outside candidate evidence, or the profile mostly shows coursework, plans, reading notes or unrelated prototypes. Use 0-54.
- Treat "planned to learn", "read about", "coursework only", "no shipped project" and "did not build" as gaps, not evidence.
- matched_evidence should cite concrete phrases from the candidate profile.
- gaps should cite important missing job requirements.

Compressed context:
{json.dumps(compressed_context, ensure_ascii=False)}
"""
        result = await self.llm.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            db=db,
            trace_name="evaluation.llm_judge_suitability",
            temperature=0,
        )
        result["_context_compression"] = compressed_context.get("context_compression")
        return result

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

    def _embedding_service_for_strategy(self, config: dict[str, Any]) -> EmbeddingService:
        if config["embedding_provider"] == "hash":
            return self.hash_embedding_service
        return self.embedding_service

    def _rank_text_chunks(
        self,
        query: str,
        chunks: list[TextChunk],
        *,
        vector_weight: float,
        lexical_weight: float,
        type_boost: bool = False,
        embedding_service: EmbeddingService | None = None,
        reranker: RerankerService | None = None,
        rerank_top_n: int = 20,
    ) -> list[dict[str, Any]]:
        embedder = embedding_service or self.hash_embedding_service
        embedding_batch = embedder.embed_texts([query] + [chunk.text for chunk in chunks])
        query_vec = embedding_batch.vectors[0] if embedding_batch.vectors else []
        chunk_vectors = embedding_batch.vectors[1:]
        query_tokens = set(tokenize(query))
        ranked = []
        for chunk, chunk_vec in zip(chunks, chunk_vectors, strict=False):
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
                    "embedding": embedding_batch.info(),
                    "scores": {
                        "vector_score": round(vector_score, 6),
                        "lexical_score": round(lexical_score, 6),
                        "first_stage_score": round(score, 6),
                    },
                }
            )
        ranked.sort(key=lambda item: item["score"], reverse=True)
        if reranker and reranker.enabled:
            first_stage = ranked[: max(rerank_top_n, 1)]
            ranked = reranker.rerank_dicts(query, first_stage, top_k=len(first_stage)) + ranked[rerank_top_n:]
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
            "difficulty_breakdown": self._summarize_pdf_by_key(per_query, "difficulty"),
            "noise_breakdown": self._summarize_pdf_by_key(per_query, "noise_profile"),
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
        embedding_providers = sorted({item.get("embedding_provider") for item in per_case if item.get("embedding_provider")})
        embedding_models = sorted({item.get("embedding_model") for item in per_case if item.get("embedding_model")})
        reranker_providers = sorted({item.get("reranker_provider") for item in per_case if item.get("reranker_provider")})
        fallback_reasons = sorted(
            {
                str(item.get("embedding_fallback_reason") or item.get("reranker_fallback_reason"))
                for item in per_case
                if item.get("embedding_fallback_reason") or item.get("reranker_fallback_reason")
            }
        )
        return {
            "strategy": strategy_name,
            "case_count": len(per_case),
            "top1_accuracy": round(sum(1 for item in per_case if item["top1_expected"]) / count, 4),
            "avg_top3_recall": round(sum(item["top3_recall"] for item in per_case) / count, 4),
            "avg_top5_recall": round(sum(item["top5_recall"] for item in per_case) / count, 4),
            "avg_mrr": round(sum(item["mrr"] for item in per_case) / count, 4),
            "avg_ndcg_at_5": round(sum(item["ndcg_at_5"] for item in per_case) / count, 4),
            "actual_embedding_providers": embedding_providers,
            "actual_embedding_models": embedding_models,
            "actual_reranker_providers": reranker_providers,
            "fallback_reasons": fallback_reasons[:3],
            "difficulty_breakdown": self._summarize_rag_by_key(per_case, "difficulty"),
        }

    def _summarize_pdf_by_key(self, rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row.get(key) or "unknown"), []).append(row)
        result = {}
        for group_key, items in sorted(grouped.items()):
            count = max(len(items), 1)
            result[group_key] = {
                "query_count": len(items),
                "top3_keyword_hit_rate": round(sum(1 for item in items if item["hit"]) / count, 4),
                "top3_page_hit_rate": round(sum(1 for item in items if item["page_hit"]) / count, 4),
                "top3_context_hit_rate": round(sum(1 for item in items if item["context_hit"]) / count, 4),
            }
        return result

    def _summarize_rag_by_key(self, rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row.get(key) or "unknown"), []).append(row)
        result = {}
        for group_key, items in sorted(grouped.items()):
            count = max(len(items), 1)
            result[group_key] = {
                "case_count": len(items),
                "top1_accuracy": round(sum(1 for item in items if item["top1_expected"]) / count, 4),
                "avg_top3_recall": round(sum(item["top3_recall"] for item in items) / count, 4),
                "avg_top5_recall": round(sum(item["top5_recall"] for item in items) / count, 4),
                "avg_mrr": round(sum(item["mrr"] for item in items) / count, 4),
                "avg_ndcg_at_5": round(sum(item["ndcg_at_5"] for item in items) / count, 4),
            }
        return result

    def _select_rag_strategy(self, strategy_results: list[dict[str, Any]]) -> dict[str, str]:
        real_embedding_results = [
            item
            for item in strategy_results
            if str(item["strategy"]).startswith("real_embedding")
            and "sentence_transformers" in item.get("actual_embedding_providers", [])
        ]
        candidates = real_embedding_results or strategy_results
        ranked = sorted(
            candidates,
            key=lambda item: (
                item["avg_top3_recall"],
                item["avg_mrr"],
                item["avg_ndcg_at_5"],
                item["top1_accuracy"],
                1 if item.get("uses_reranker") else 0,
                1 if str(item["strategy"]).startswith("real_embedding") else 0,
                0 if item["strategy"] in {"hash_lexical_only", "lexical_only"} else 1,
            ),
            reverse=True,
        )
        selected = ranked[0]
        provider_note = ", ".join(selected.get("actual_embedding_providers") or [])
        reranker_note = ", ".join(selected.get("actual_reranker_providers") or []) or "none"
        hash_results = [item for item in strategy_results if str(item["strategy"]).startswith("hash_")]
        baseline_candidates = hash_results or strategy_results
        baseline_best = sorted(
            baseline_candidates,
            key=lambda item: (item["avg_top3_recall"], item["avg_mrr"], item["avg_ndcg_at_5"], item["top1_accuracy"]),
            reverse=True,
        )[0]
        return {
            "strategy": selected["strategy"],
            "reason": (
                f"{selected['strategy']} 的 Top3 Recall={selected['avg_top3_recall']}、"
                f"MRR={selected['avg_mrr']}、nDCG@5={selected['avg_ndcg_at_5']} 综合最高；"
                f"实际 embedding={provider_note}，reranker={reranker_note}。"
                "该选择在真实 embedding 策略内优先保证技术关键词召回；"
                f"hash baseline 最优为 {baseline_best['strategy']}，仅作为离线基线对照。"
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
