import asyncio
import json
from pathlib import Path

import pytest

from app.models.entities import Job, JobChunk
from app.services.job_sources import JobPosting
from app.services.interview_sources import InterviewExperienceSearchResult
from app.services.evaluation_service import EvaluationService


def test_sample_evaluation_produces_quantitative_metrics(db_session):
    run = asyncio.run(EvaluationService().run_sample_evaluation(db_session))

    assert run.summary_json["case_count"] >= 3
    assert 0 <= run.summary_json["pass_rate"] <= 1
    assert "avg_required_skill_recall" in run.summary_json
    assert run.case_results_json[0]["overall_score"] >= 0


def test_llm_workflow_fit_rubric_v2_has_auditable_label_bands():
    cases = json.loads(Path("evals/llm_workflow_cases.json").read_text(encoding="utf-8"))
    bands = {
        "weak_fit": (0, 54),
        "partial_fit": (55, 84),
        "strong_fit": (85, 100),
    }
    reviewed = {
        "analytics_candidate_weak_recommendation_role",
        "beginner_candidate_weak_agent_role",
        "zh_agent_candidate_partial_worker_gap",
        "zh_agent_candidate_weak_planned_learning",
        "zh_agent_resume_with_prompt_injection_noise",
        "zh_agent_pdf_layout_noise_partial",
        "agent_candidate_partial_llm_eval_role",
        "cv_candidate_partial_ml_platform_role",
    }

    for case in cases:
        low, high = case["expected_fit_score_range"]
        band_low, band_high = bands[case["expected_fit_label"]]
        assert band_low <= low <= high <= band_high, case["name"]
        if case["name"] in reviewed:
            assert case["annotation_version"] == "fit-rubric-v2"
            assert len(case["annotation_rationale"]) >= 20


def test_fit_output_contract_separates_delivery_from_coursework():
    service = EvaluationService()

    delivery_errors = service._fit_output_contract_errors(
        {"fit_label": "weak_fit", "fit_score": 40},
        {
            "matched_required_skill_count": 3,
            "has_related_delivery_evidence": True,
            "has_coursework_or_planned_only_evidence": False,
        },
    )
    coursework_errors = service._fit_output_contract_errors(
        {"fit_label": "partial_fit", "fit_score": 60},
        {
            "matched_required_skill_count": 3,
            "has_related_delivery_evidence": False,
            "has_coursework_or_planned_only_evidence": True,
        },
    )

    assert any("不应判为 weak_fit" in item for item in delivery_errors)
    assert any("coursework" in item for item in coursework_errors)


def test_fit_output_contract_rejects_strong_fit_with_low_required_skill_coverage():
    errors = EvaluationService()._fit_output_contract_errors(
        {"fit_label": "strong_fit", "fit_score": 90},
        {
            "matched_required_skill_count": 3,
            "has_related_delivery_evidence": True,
            "has_coursework_or_planned_only_evidence": False,
            "required_skill_coverage": 0.5,
        },
    )

    assert any("0.67" in item for item in errors)


def test_evaluation_dataset_rejects_single_object_root():
    class InMemoryDatasetPath:
        name = "single-case.json"

        @staticmethod
        def read_text(*, encoding):
            assert encoding == "utf-8"
            return json.dumps({"name": "only-one"})

    with pytest.raises(ValueError, match="根节点必须是 JSON 数组"):
        EvaluationService._load_case_dataset(InMemoryDatasetPath())


def test_agent_full_flow_subset_gate_does_not_require_absent_negative_case():
    passing_case = {
        "case_passed": True,
        "status": "completed",
        "top_job_passed": True,
        "score_passed": True,
        "trace_passed": True,
        "artifact_passed": True,
        "langgraph_passed": True,
        "fit_gate_blocked": False,
        "expected_fit_gate_blocked": False,
    }

    summary = EvaluationService()._summarize_agent_full_flow(
        [passing_case],
        Path("positive-subset.json"),
    )

    assert summary["expected_fit_gate_block_count"] == 0
    assert "fit_gate_block_count" not in {
        item["metric"] for item in summary["release_gate"]["checks"]
    }


def test_pdf_chunk_strategy_evaluation_selects_strategy(db_session):
    run = EvaluationService().run_pdf_chunk_strategy_evaluation(db_session)

    assert run.summary_json["case_count"] >= 90
    assert run.summary_json["query_count"] >= 500
    assert run.summary_json["selected_strategy"]
    assert len(run.summary_json["strategy_results"]) >= 4
    assert "difficulty_breakdown" in run.summary_json["strategy_results"][0]
    assert "noise_breakdown" in run.summary_json["strategy_results"][0]
    assert isinstance(run.summary_json["release_gate"]["passed"], bool)
    assert len(run.summary_json["release_gate"]["checks"]) == 5


def test_rag_strategy_evaluation_selects_strategy(db_session):
    run = EvaluationService().run_rag_strategy_evaluation(db_session)

    assert run.summary_json["case_count"] >= 180
    assert run.summary_json["selected_strategy"]
    assert "vector_store_selection" in run.summary_json
    assert "embedding_model_selection" in run.summary_json
    assert "reranker_selection" in run.summary_json
    assert len(run.summary_json["strategy_results"]) >= 4
    assert any(item["uses_reranker"] for item in run.summary_json["strategy_results"])
    assert "difficulty_breakdown" in run.summary_json["strategy_results"][0]
    gate_checks = {item["metric"]: item for item in run.summary_json["release_gate"]["checks"]}
    assert "selected_metrics.actual_embedding_providers" in gate_checks
    assert "selected_metrics.actual_reranker_providers" in gate_checks
    assert isinstance(run.summary_json["release_gate"]["passed"], bool)
    assert "理论上限" in run.summary_json["release_gate_basis"]


def test_agent_full_flow_evaluation_covers_orchestrator_components(db_session):
    run = asyncio.run(EvaluationService().run_agent_full_flow_evaluation(db_session))

    assert run.summary_json["evaluation_type"] == "agent_full_flow"
    assert run.summary_json["case_count"] >= 6
    assert run.summary_json["pass_rate"] == 1.0
    assert run.summary_json["top_job_accuracy"] == 1.0
    assert run.summary_json["score_gate_accuracy"] == 1.0
    assert run.summary_json["quick_apply_pass_rate"] == 1.0
    assert run.summary_json["application_packet_pass_rate"] == 1.0
    assert run.summary_json["trace_pass_rate"] == 1.0
    assert run.summary_json["artifact_pass_rate"] == 1.0
    assert run.summary_json["langgraph_pass_rate"] == 1.0
    assert run.summary_json["fit_gate_block_count"] >= 3
    assert run.summary_json["release_gate"]["passed"] is True
    assert any(item.get("fit_gate_blocked") for item in run.case_results_json)
    assert all(item.get("run_trace") for item in run.case_results_json)
    assert all(item.get("langgraph_passed") for item in run.case_results_json)


def test_jd_parser_evaluation_covers_noisy_realistic_cases(db_session):
    run = asyncio.run(EvaluationService().run_jd_parser_evaluation(db_session))

    assert run.summary_json["evaluation_type"] == "jd_parser"
    assert run.summary_json["case_count"] >= 30
    assert run.summary_json["completed_rate"] == 1.0
    assert run.summary_json["pass_rate"] >= 0.9
    assert run.summary_json["avg_required_skill_recall"] >= 0.85
    assert run.summary_json["avg_required_skill_precision"] >= 0.9
    assert run.summary_json["avg_required_skill_f1"] >= 0.85
    assert run.summary_json["grounding_quality_gate_pass_rate"] >= 0.95
    assert run.summary_json["avg_keyword_hit_rate"] >= 0.85
    assert run.summary_json["absent_required_skill_violation_count"] == 0
    assert "difficulty_breakdown" in run.summary_json
    assert "noise_breakdown" in run.summary_json
    assert {"easy", "medium", "hard", "adversarial"} <= set(run.summary_json["difficulty_breakdown"])
    assert run.summary_json["release_gate"]["passed"] is True


def test_job_relevance_evaluation_quantifies_chinese_ranking_quality(db_session):
    run = EvaluationService().run_job_relevance_evaluation(db_session)

    assert run.summary_json["evaluation_type"] == "job_relevance_ranking"
    assert run.summary_json["case_count"] >= 12
    assert run.summary_json["candidate_count"] >= 120
    assert run.summary_json["pass_rate"] >= 0.9
    assert run.summary_json["top1_accuracy"] >= 0.9
    assert run.summary_json["avg_top3_recall"] >= 0.9
    assert run.summary_json["avg_mrr"] >= 0.9
    assert run.summary_json["avg_ndcg_at_5"] >= 0.9
    assert run.summary_json["low_grade_above_strong_count"] == 0
    assert run.summary_json["release_gate"]["passed"] is True
    assert "agent_dev_intern" in run.summary_json["intent_breakdown"]
    assert any("实习" in item["query"] for item in run.case_results_json)
    assert all(item["ranked_jobs"][0]["grade"] >= 3 for item in run.case_results_json)
    assert run.case_results_json[0]["ranked_jobs"][0]["reasons"]


def test_application_packet_evaluation_catches_fabrication_and_boundary_risks(db_session):
    run = EvaluationService().run_application_packet_evaluation(db_session)

    assert run.summary_json["evaluation_type"] == "application_packet_guardrail"
    assert run.summary_json["case_count"] >= 26
    assert run.summary_json["pass_rate"] == 1.0
    assert run.summary_json["high_risk_recall"] == 1.0
    assert run.summary_json["false_block_count"] == 0
    assert run.summary_json["missed_high_risk_count"] == 0
    assert run.summary_json["false_block_rate"] == 0
    assert run.summary_json["missed_high_risk_rate"] == 0
    assert run.summary_json["issue_code_hit_rate"] == 1.0
    assert run.summary_json["release_gate"]["passed"] is True
    assert "unsupported_claims" in {
        code for item in run.case_results_json for code in item.get("actual_issue_codes", [])
    }
    assert any("missing_apply_url" in item.get("warning_codes", []) for item in run.case_results_json)


def test_natural_language_plan_evaluation_checks_actions_and_negations(db_session):
    dataset = Path(__file__).parent / "fixtures" / "natural_language_plan_cases.json"

    class FakePlannerLLM:
        available = True

        async def generate_json(self, **kwargs):
            if "没有简历" in kwargs["user_prompt"]:
                return {
                    "intent": "search_jobs",
                    "query": "Agent RAG 实习",
                    "profile": None,
                    "job": None,
                    "needs_profile": False,
                    "needs_job": False,
                    "actions": ["search_jobs"],
                    "reason": "先浏览岗位",
                }
            return {
                "intent": "quick_apply",
                "query": "Agent 开发实习生",
                "profile": None,
                "job": None,
                "needs_profile": True,
                "needs_job": True,
                "actions": ["quick_apply"],
                "reason": "错误地选择了投递",
            }

    service = EvaluationService()
    service.llm = FakePlannerLLM()
    run = asyncio.run(service.run_natural_language_plan_evaluation(db_session, dataset_path=dataset))

    assert run.summary_json["pass_rate"] == 1.0
    assert run.summary_json["forbidden_action_violation_count"] == 0
    assert run.summary_json["intent_accuracy"] == 1.0


def test_interview_prep_evaluation_covers_sources_stack_and_gap_drills(db_session):
    run = EvaluationService().run_interview_prep_evaluation(db_session)

    assert run.summary_json["evaluation_type"] == "interview_prep"
    assert run.summary_json["case_count"] >= 8
    assert run.summary_json["pass_rate"] == 1.0
    assert run.summary_json["research_source_pass_rate"] == 1.0
    assert run.summary_json["gap_drill_pass_rate"] == 1.0
    assert run.summary_json["source_backed_pass_rate"] == 1.0
    assert run.summary_json["question_id_pass_rate"] == 1.0
    assert run.summary_json["source_perspective_pass_rate"] == 1.0
    assert run.summary_json["preparation_angle_pass_rate"] == 1.0
    assert run.summary_json["llm_question_generation_pass_rate"] == 1.0
    assert run.summary_json["question_quality_pass_rate"] == 1.0
    assert run.summary_json["avg_question_quality_score"] >= 0.82
    assert run.summary_json["markdown_export_pass_rate"] == 1.0
    assert run.summary_json["avg_source_backed_question_count"] > 0
    assert run.summary_json["avg_question_count"] == 10
    assert run.summary_json["release_gate"]["passed"] is True
    assert "agent_development" in run.summary_json["role_type_breakdown"]


def test_interview_source_smoke_records_platform_health(db_session):
    class HealthySource:
        name = "healthy_nowcoder"

        async def search(self, *, query: str, limit: int):
            return [
                InterviewExperienceSearchResult(
                    source=self.name,
                    title="腾讯 Agent 开发实习面经 一面",
                    url="https://example.com/nowcoder/agent-interview",
                    snippet=f"{query}：一面问 RAG 召回率、FastAPI 并发和 Agent Trace。",
                )
            ][:limit]

    class BrokenSource:
        name = "blocked_xhs"

        async def search(self, *, query: str, limit: int):
            raise RuntimeError("login required")

    class FakeRegistry:
        def select(self, names=None):
            return [HealthySource(), BrokenSource()]

    run = asyncio.run(
        EvaluationService().run_interview_source_smoke(
            db_session,
            query="Agent 开发实习生 面经",
            limit=3,
            source_registry=FakeRegistry(),
        )
    )

    assert run.summary_json["evaluation_type"] == "interview_source_smoke"
    assert run.summary_json["status"] == "completed_with_source_errors"
    assert run.summary_json["reachable_source_rate"] == 0.5
    assert run.summary_json["result_source_rate"] == 0.5
    assert run.summary_json["total_result_count"] == 1
    assert run.summary_json["interview_signal_rate"] == 1.0
    assert run.summary_json["query_relevance_rate"] == 1.0
    assert run.summary_json["content_extractable_rate"] == 1.0
    assert run.summary_json["core_regression_independent"] is True
    assert "blocked_xhs" in run.summary_json["source_errors"]
    assert run.case_results_json[0]["sample_experiences"][0]["interview_signal"] is True


def test_interview_source_smoke_marks_empty_and_low_quality_sources(db_session):
    class LowQualitySource:
        name = "low_quality"

        async def search(self, *, query: str, limit: int):
            return [
                InterviewExperienceSearchResult(
                    source=self.name,
                    title="校园生活分享",
                    url="https://example.com/post",
                    snippet="校园社团活动记录，和岗位技术内容无关。",
                )
            ]

    class EmptySource:
        name = "empty"

        async def search(self, *, query: str, limit: int):
            return []

    class FakeRegistry:
        def select(self, names=None):
            return [LowQualitySource(), EmptySource()]

    run = asyncio.run(
        EvaluationService().run_interview_source_smoke(
            db_session,
            query="Agent 开发实习生 面经",
            source_registry=FakeRegistry(),
        )
    )

    assert run.summary_json["status"] == "completed_with_low_quality_results"
    assert run.summary_json["reachable_source_rate"] == 1.0
    assert run.summary_json["result_source_rate"] == 0.5
    assert run.summary_json["interview_signal_rate"] == 0.0
    assert run.summary_json["source_empty"] == ["empty"]


def test_jd_parser_aliases_preferred_and_negative_context():
    from app.services.jd_parser import JDParserService

    jd = (
        "RAG Evaluation Intern\n"
        "Responsibilities: improve retrieval augmented generation over a vector database.\n"
        "Responsibilities: build guardrail and prompt regression checks for LLM answers.\n"
        "Requirements: Python, SQL, RAG, Evaluation, Tool Calling and A/B tests.\n"
        "Requirements: No prior Kubernetes or MLflow experience required.\n"
        "Preferred: LangGraph and MCP exposure are helpful but optional."
    )

    parsed = JDParserService().heuristic_parse(jd, title="RAG Evaluation Intern")

    assert {"RAG", "Vector Database", "Guardrail", "Prompt Regression", "LLM", "Tool Calling", "A/B Testing"} <= set(
        parsed["required_skills"]
    )
    assert "Kubernetes" not in parsed["required_skills"]
    assert "MLflow" not in parsed["required_skills"]
    assert {"LangGraph", "MCP"} <= set(parsed["preferred_skills"])


def test_jd_parser_promotes_general_evaluation_experience_to_required_skill():
    from app.services.jd_parser import JDParserService

    parsed = JDParserService().heuristic_parse(
        "Agent 应用开发实习生\n负责 Agent 工具调用、RAG 引用和 trace 平台。"
        "要求 Python、FastAPI、RAG 和评测经验。",
        title="Agent 应用开发实习生",
    )

    assert "Model Evaluation" in parsed["required_skills"]


def test_jd_parser_filters_llm_keywords_not_present_in_jd():
    from app.services.jd_parser import JDParserService

    parsed, rejected = JDParserService()._filter_unsupported_optional_keywords(
        {"keywords": ["Python", "RankWorks", "RAG"]},
        raw_text="要求 Python 和 RAG 经验。",
    )

    assert parsed["keywords"] == ["Python", "RAG"]
    assert rejected == ["RankWorks"]


def test_jd_parser_llm_merge_preserves_heuristic_skills():
    from app.services.jd_parser import JDParserService

    service = JDParserService()
    heuristic = service.heuristic_parse(
        "Agent Development Intern\n"
        "Responsibilities: build AI agents with Python, FastAPI and RAG.\n"
        "Requirements: SQL and evaluation.",
        title="Agent Development Intern",
    )
    sparse_llm = {
        "title": "Agent Development Intern",
        "required_skills": ["Python", "SQL"],
        "preferred_skills": [],
        "responsibilities": ["Build tools."],
        "qualifications": ["Python and SQL."],
        "keywords": ["Python", "SQL"],
    }

    merged = service._merge_llm_parse(heuristic, sparse_llm)

    assert {"Agent", "Python", "FastAPI", "RAG", "SQL", "Evaluation"} <= set(merged["required_skills"])
    assert merged["required_skills"].count("Python") == 1


def test_jd_parser_canonicalizes_chinese_aliases_and_job_type():
    from app.services.jd_parser import JDParserService

    parsed = JDParserService()._canonicalize_structured_jd(
        {
            "job_type": "实习",
            "required_skills": ["大语言模型", "LLM", "提示词工程", "模型评测"],
            "preferred_skills": ["Prompt Engineering", "A/B实验"],
        }
    )

    assert parsed["job_type"] == "internship"
    assert parsed["required_skills"] == ["LLM", "Prompt Engineering", "Model Evaluation"]
    assert parsed["preferred_skills"] == ["A/B Testing"]

    english_job_type = JDParserService()._canonicalize_structured_jd(
        {"job_type": "Intern", "required_skills": [], "preferred_skills": []}
    )
    assert english_job_type["job_type"] == "internship"


def test_jd_parser_distinguishes_prompt_regression_from_prompt_engineering():
    from app.services.jd_parser import JDParserService

    parsed = JDParserService().heuristic_parse(
        "Requirements: Python, SQL, prompt regression and model quality analysis.",
        title="LLM Evaluation Intern",
    )

    assert "Prompt Regression" in parsed["required_skills"]
    assert "Prompt Engineering" not in parsed["required_skills"]


def test_fit_message_is_composed_only_from_verified_structured_facts():
    message, facts = EvaluationService._publish_grounded_fit_message(
        {
            "matched_evidence": ["使用 FastAPI 构建异步接口", "完成 RAG 召回评测"],
            "gaps": ["岗位要求 Redis worker 经验"],
            "message_to_candidate": "你没有生产级经验，因此不建议投递。",
        }
    )

    assert "使用 FastAPI 构建异步接口" in message
    assert "岗位要求 Redis worker 经验" in message
    assert "没有生产级经验" not in message
    assert facts == ["使用 FastAPI 构建异步接口", "完成 RAG 召回评测", "岗位要求 Redis worker 经验"]


def test_jd_parser_llm_merge_demotes_soft_requirement_skills():
    from app.services.jd_parser import JDParserService

    service = JDParserService()
    raw_jd = (
        "Agent 开发实习生\n"
        "职责：实现 RAG 流程，包括 chunk、embedding 检索和 reranker 二阶段排序。\n"
        "要求：熟悉 Python、FastAPI、SQLite、RAG、Embedding、Reranker、Prompt Engineering。\n"
        "加分项：有 MLflow、Kubernetes 经验者优先，但不是硬性要求。"
    )
    heuristic = service.heuristic_parse(raw_jd, title="Agent 开发实习生")
    noisy_llm = {
        "title": "Agent 开发实习生",
        "required_skills": [
            "Python",
            "FastAPI",
            "SQLite",
            "RAG",
            "Embedding",
            "Reranker",
            "Prompt Engineering",
            "MLflow",
            "Kubernetes",
        ],
        "preferred_skills": ["MLflow", "Kubernetes"],
        "responsibilities": ["实现 RAG 流程"],
        "qualifications": ["熟悉 Python、FastAPI、SQLite、RAG、Embedding、Reranker、Prompt Engineering"],
        "keywords": ["Agent", "RAG", "MLflow", "Kubernetes"],
    }

    merged = service._merge_llm_parse(heuristic, noisy_llm, raw_text=raw_jd)

    assert {"Python", "FastAPI", "SQLite", "RAG", "Embedding", "Reranker", "Prompt Engineering"} <= set(
        merged["required_skills"]
    )
    assert "MLflow" not in merged["required_skills"]
    assert "Kubernetes" not in merged["required_skills"]
    assert {"MLflow", "Kubernetes"} <= set(merged["preferred_skills"])


def test_jd_parser_retries_transient_empty_llm_response():
    from app.services.jd_parser import JDParserService

    class FakeLLM:
        available = True

        def __init__(self) -> None:
            self.trace_names: list[str] = []

        async def generate_text(self, **kwargs):
            self.trace_names.append(kwargs["trace_name"])
            if len(self.trace_names) == 1:
                raise RuntimeError("LLM returned empty content.")
            return json.dumps(
                {
                    "title": "Agent 开发实习生",
                    "required_skills": ["Python", "FastAPI", "RAG"],
                    "preferred_skills": [],
                    "responsibilities": ["开发 Agent 应用"],
                    "qualifications": ["熟悉 Python、FastAPI 和 RAG"],
                    "keywords": ["Agent", "Python", "FastAPI", "RAG"],
                },
                ensure_ascii=False,
            )

    service = JDParserService()
    fake_llm = FakeLLM()
    service.llm = fake_llm

    parsed = asyncio.run(
        service.parse_jd(
            "Agent 开发实习生\n要求：熟悉 Python、FastAPI 和 RAG。",
            title="Agent 开发实习生",
        )
    )

    assert fake_llm.trace_names == ["jd_parser.parse_jd", "jd_parser.parse_jd.retry_1"]
    assert {"Python", "FastAPI", "RAG"} <= set(parsed["required_skills"])


def test_jd_parser_repairs_truncated_json_response():
    from app.services.jd_parser import JDParserService

    class FakeLLM:
        available = True

        def __init__(self) -> None:
            self.trace_names: list[str] = []

        async def generate_text(self, **kwargs):
            self.trace_names.append(kwargs["trace_name"])
            if kwargs["trace_name"] == "jd_parser.parse_jd":
                return '{"title":"Frontend Design System Intern","company":"Demo UI","location":null'
            return json.dumps(
                {
                    "title": "Frontend Design System Intern",
                    "company": "Demo UI",
                    "location": None,
                    "job_type": "internship",
                    "required_skills": ["React", "TypeScript", "CSS"],
                    "preferred_skills": [],
                    "responsibilities": ["Build React components and CSS token systems."],
                    "qualifications": ["React, TypeScript and CSS."],
                    "keywords": ["React", "TypeScript", "CSS"],
                    "seniority": "intern",
                },
                ensure_ascii=False,
            )

    service = JDParserService()
    fake_llm = FakeLLM()
    service.llm = fake_llm

    parsed = asyncio.run(
            service.parse_jd(
                "Frontend Design System Intern. Responsibilities: Build React components and CSS token systems. "
                "Requirements: React, TypeScript, CSS.",
            title="Frontend Design System Intern",
            company="Demo UI",
        )
    )

    assert fake_llm.trace_names == ["jd_parser.parse_jd", "jd_parser.parse_jd.repair_json"]
    assert {"React", "TypeScript", "CSS"} <= set(parsed["required_skills"])


def test_jd_parser_allows_two_transient_retries():
    from app.services.jd_parser import JDParserService

    class FakeLLM:
        available = True

        def __init__(self) -> None:
            self.trace_names: list[str] = []

        async def generate_text(self, **kwargs):
            self.trace_names.append(kwargs["trace_name"])
            if len(self.trace_names) < 3:
                raise RuntimeError("LLM returned empty content.")
            return json.dumps(
                {
                    "title": "LLM Evaluation Intern",
                    "company": "Demo AI",
                    "location": None,
                    "job_type": "internship",
                    "required_skills": ["Python", "SQL", "Evaluation"],
                    "preferred_skills": [],
                    "responsibilities": ["Build model quality evaluation workflows."],
                    "qualifications": ["Python, SQL and evaluation."],
                    "keywords": ["Python", "SQL", "Evaluation"],
                    "seniority": "intern",
                },
                ensure_ascii=False,
            )

    service = JDParserService()
    fake_llm = FakeLLM()
    service.llm = fake_llm

    parsed = asyncio.run(
            service.parse_jd(
                "LLM Evaluation Intern. Responsibilities: Build model quality evaluation workflows. "
                "Requirements: Python, SQL, evaluation.",
            title="LLM Evaluation Intern",
        )
    )

    assert fake_llm.trace_names == ["jd_parser.parse_jd", "jd_parser.parse_jd.retry_1", "jd_parser.parse_jd.retry_2"]
    assert {"Python", "SQL", "Evaluation"} <= set(parsed["required_skills"])


def test_real_job_source_smoke_records_source_layer_metrics(db_session):
    class HealthySource:
        name = "healthy"

        async def search(self, *, query: str, location: str | None, limit: int):
            return [
                JobPosting(
                    source=self.name,
                    external_id="healthy-1",
                    title="Agent Development Intern",
                    company="Example AI",
                    location=location or "Shanghai",
                    job_type="internship",
                    apply_url="https://example.com/jobs/healthy-1",
                    raw_jd_text=f"{query}: build Agent workflows with FastAPI and RAG.",
                )
            ][:limit]

    class BrokenSource:
        name = "broken"

        async def search(self, *, query: str, location: str | None, limit: int):
            raise RuntimeError("source timeout")

    class FakeRegistry:
        def select(self, names=None):
            return [HealthySource(), BrokenSource()]

    run = asyncio.run(
        EvaluationService().run_real_job_source_smoke(
            db_session,
            query="Agent Development Intern",
            location="Shanghai",
            limit=5,
            source_registry=FakeRegistry(),
        )
    )

    assert run.summary_json["evaluation_type"] == "real_job_source_smoke"
    assert run.summary_json["status"] == "completed_with_source_errors"
    assert run.summary_json["reachable_source_rate"] == 0.5
    assert run.summary_json["result_source_rate"] == 0.5
    assert run.summary_json["total_result_count"] == 1
    assert run.summary_json["non_empty_jd_rate"] == 1.0
    assert run.summary_json["apply_url_rate"] == 1.0
    assert run.summary_json["query_relevance_rate"] == 1.0
    assert run.summary_json["agent_related_rate"] == 1.0
    assert run.summary_json["core_regression_independent"] is True
    assert any(item["status"] == "source_error" and item["error"] for item in run.case_results_json)
    assert run.case_results_json[0]["sample_jobs"][0]["agent_related"] is True


def test_real_job_source_smoke_marks_empty_sources(db_session):
    class HealthySource:
        name = "healthy"

        async def search(self, *, query: str, location: str | None, limit: int):
            return [
                JobPosting(
                    source=self.name,
                    external_id="healthy-1",
                    title="Agent Development Intern",
                    company="Example AI",
                    location=location,
                    job_type="internship",
                    apply_url="https://example.com/jobs/healthy-1",
                    raw_jd_text="Build Agent workflows.",
                )
            ]

    class EmptySource:
        name = "empty"

        async def search(self, *, query: str, location: str | None, limit: int):
            return []

    class FakeRegistry:
        def select(self, names=None):
            return [HealthySource(), EmptySource()]

    run = asyncio.run(
        EvaluationService().run_real_job_source_smoke(
            db_session,
            source_registry=FakeRegistry(),
        )
    )

    assert run.summary_json["status"] == "completed_with_empty_sources"
    assert run.summary_json["reachable_source_rate"] == 1.0
    assert run.summary_json["result_source_rate"] == 0.5


def test_real_job_ingest_smoke_parses_stores_chunks_and_retrieves(db_session):
    class HealthySource:
        name = "healthy"

        async def search(self, *, query: str, location: str | None, limit: int):
            return [
                JobPosting(
                    source=self.name,
                    external_id="ingest-1",
                    title="Agent Development Intern",
                    company="Example AI",
                    location=location or "Shanghai",
                    job_type="internship",
                    apply_url="https://example.com/jobs/ingest-1",
                    raw_jd_text=(
                        "Agent Development Intern\n"
                        "Responsibilities: build Agent workflows with Python, FastAPI, RAG and SQLite.\n"
                        "Requirements: evaluation, guardrails, retrieval and tool calling."
                    ),
                )
            ][:limit]

    class FakeRegistry:
        def select(self, names=None):
            return [HealthySource()]

    run = asyncio.run(
        EvaluationService().run_real_job_ingest_smoke(
            db_session,
            query="Agent Development Intern",
            location="Shanghai",
            limit=2,
            source_registry=FakeRegistry(),
        )
    )

    assert run.summary_json["evaluation_type"] == "real_job_ingest_smoke"
    assert run.summary_json["status"] == "completed"
    assert run.summary_json["parse_success_rate"] == 1.0
    assert run.summary_json["ingest_success_rate"] == 1.0
    assert run.summary_json["chunk_index_success_rate"] == 1.0
    assert run.summary_json["retrieval_probe_success_rate"] == 1.0
    assert run.summary_json["parser_quality_evaluable_count"] == 1
    assert run.summary_json["parser_quality_pass_rate"] == 1.0
    assert run.summary_json["avg_parser_quality_required_recall"] >= 0.8
    assert run.summary_json["avg_parser_quality_query_coverage"] == 1.0
    assert run.summary_json["avg_chunks_per_job"] > 0
    assert run.summary_json["embedding_provider_counts"].get("hash", 0) > 0
    assert run.summary_json["retrieval_query_embedding_provider_counts"].get("hash", 0) > 0
    assert db_session.query(Job).filter(Job.external_id == "ingest-1").count() == 1
    assert db_session.query(JobChunk).count() > 0
    job_result = run.case_results_json[0]["job_results"][0]
    assert job_result["required_skill_count"] > 0
    assert job_result["parser_quality_probe_passed"] is True
    assert "Agent" in job_result["parser_quality_expected_skills"]
    assert job_result["retrieved_chunk_preview"]


def test_real_job_ingest_smoke_reports_parser_quality_failures(db_session):
    class HealthySource:
        name = "healthy"

        async def search(self, *, query: str, location: str | None, limit: int):
            return [
                JobPosting(
                    source=self.name,
                    external_id="quality-fail-1",
                    title="Agent Development Intern",
                    company="Example AI",
                    location=location or "Shanghai",
                    job_type="internship",
                    apply_url="https://example.com/jobs/quality-fail-1",
                    raw_jd_text=(
                        "Agent Development Intern\n"
                        "Responsibilities: build Agent workflows with RAG, LLM and tool calling.\n"
                        "Requirements: Python, FastAPI, RAG, LLM, Evaluation and Guardrail."
                    ),
                )
            ][:limit]

    class FakeRegistry:
        def select(self, names=None):
            return [HealthySource()]

    class WeakParser:
        async def parse_jd(self, raw_text: str, **kwargs):
            return {
                "title": kwargs.get("title"),
                "company": kwargs.get("company"),
                "location": kwargs.get("location"),
                "job_type": "internship",
                "required_skills": ["Python"],
                "preferred_skills": [],
                "responsibilities": ["Build backend scripts."],
                "qualifications": ["Python."],
                "keywords": ["Python"],
                "seniority": "intern",
            }

    service = EvaluationService()
    service.jd_parser = WeakParser()

    run = asyncio.run(
        service.run_real_job_ingest_smoke(
            db_session,
            query="Agent Development Intern",
            source_registry=FakeRegistry(),
        )
    )

    assert run.summary_json["status"] == "completed_with_parser_quality_failures"
    assert run.summary_json["parse_success_rate"] == 1.0
    assert run.summary_json["ingest_success_rate"] == 1.0
    assert run.summary_json["parser_quality_pass_rate"] == 0.0
    assert run.summary_json["parser_quality_failure_count"] == 1
    assert run.summary_json["parser_quality_failure_breakdown"]["required_recall_below_threshold"] == 1
    job_result = run.case_results_json[0]["job_results"][0]
    assert job_result["status"] == "completed"
    assert job_result["parser_quality_probe_passed"] is False
    assert {"Agent", "RAG", "LLM"} <= set(job_result["parser_quality_missing_required_skills"])


def test_real_job_ingest_smoke_records_parse_errors(db_session):
    class HealthySource:
        name = "healthy"

        async def search(self, *, query: str, location: str | None, limit: int):
            return [
                JobPosting(
                    source=self.name,
                    external_id="parse-fail-1",
                    title="Agent Development Intern",
                    company="Example AI",
                    location=location,
                    job_type="internship",
                    apply_url="https://example.com/jobs/parse-fail-1",
                    raw_jd_text="This JD will fail parser.",
                )
            ]

    class FakeRegistry:
        def select(self, names=None):
            return [HealthySource()]

    class BrokenParser:
        async def parse_jd(self, *args, **kwargs):
            raise RuntimeError("parser unavailable")

    service = EvaluationService()
    service.jd_parser = BrokenParser()

    run = asyncio.run(
        service.run_real_job_ingest_smoke(
            db_session,
            source_registry=FakeRegistry(),
        )
    )

    assert run.summary_json["status"] == "completed_with_ingest_failures"
    assert run.summary_json["parse_success_rate"] == 0.0
    assert run.summary_json["ingest_success_rate"] == 0.0
    assert run.summary_json["failure_breakdown"] == {"parse_error": 1}
    assert run.case_results_json[0]["job_results"][0]["status"] == "parse_error"


def test_llm_workflow_dataset_covers_full_pipeline():
    cases = json.loads(Path("evals/llm_workflow_cases.json").read_text(encoding="utf-8"))
    required_fields = {
        "name",
        "difficulty",
        "resume_raw_text",
        "expected_profile_skills",
        "expected_profile_keywords",
        "job",
        "expected_jd_skills",
        "expected_fit_label",
        "expected_fit_score_range",
        "run_tailor",
        "expected_tailored_keywords",
        "forbidden_tailored_claims",
    }

    assert len(cases) >= 18
    assert required_fields <= set(cases[0])
    assert {case["expected_fit_label"] for case in cases} == {"strong_fit", "partial_fit", "weak_fit"}
    assert sum(1 for case in cases if case["run_tailor"]) >= 10
    assert {"easy", "medium", "hard", "adversarial"} <= {case["difficulty"] for case in cases}


def test_llm_workflow_summary_has_quantitative_metrics():
    service = EvaluationService()
    case_results = [
        {
            "name": "strong_case",
            "difficulty": "easy",
            "run_tailor": True,
            "status": "completed",
            "case_passed": True,
            "resume_parse_success": True,
            "profile_skill_recall": 1.0,
            "profile_keyword_hit_rate": 0.8,
            "jd_parse_success": True,
            "jd_skill_recall": 1.0,
            "fit_judge_success": True,
            "label_passed": True,
            "fit_score_in_expected_range": True,
            "fit_score_range_error": 0,
            "matcher_evidence_hit_rate": 0.75,
            "tailor_success": True,
            "tailor_passed": True,
            "tailored_keyword_hit_rate": 0.83,
            "guardrail_passed": True,
            "forbidden_claim_free": True,
            "hallucination_count": 0,
            "fit_context_compression": {"reduction_ratio": 0.4},
            "tailor_context_compression": {"reduction_ratio": 0.5, "retained_evidence_count": 8},
        },
        {
            "name": "failed_case",
            "difficulty": "adversarial",
            "run_tailor": False,
            "status": "failed",
            "failed_stage": "jd_parse",
            "case_passed": False,
            "resume_parse_success": True,
            "profile_skill_recall": 0.5,
            "profile_keyword_hit_rate": 0.5,
        },
    ]

    summary = service._summarize_llm_workflow(case_results, Path("evals/llm_workflow_cases.json"))

    assert summary["case_count"] == 2
    assert summary["completed_rate"] == 0.5
    assert summary["end_to_end_pass_rate"] == 0.5
    assert summary["resume_parse_success_rate"] == 1.0
    assert summary["jd_parse_success_rate"] == 0.5
    assert summary["fit_label_accuracy"] == 1.0
    assert summary["tailor_pass_rate"] == 1.0
    assert summary["failed_stage_breakdown"] == {"jd_parse": 1}
    assert summary["context_compression"]["fit_context_count"] == 1
    assert summary["context_compression"]["avg_tailor_reduction_ratio"] == 0.5
    assert "difficulty_breakdown" in summary
    assert summary["difficulty_breakdown"]["adversarial"]["tailor_pass_rate"] is None
    assert summary["difficulty_breakdown"]["adversarial"]["guardrail_pass_rate"] is None


def test_forbidden_claim_hits_ignore_negated_disclosures():
    service = EvaluationService()

    conservative_text = "Built metric dashboards. Did not implement ranking models or CTR features."
    assert service._forbidden_claim_hits(conservative_text, ["ranking model", "CTR feature"]) == []

    inflated_text = "Implemented ranking models and owned CTR feature engineering for recommender systems."
    assert service._forbidden_claim_hits(inflated_text, ["ranking model", "CTR feature engineering"]) == [
        "ranking model",
        "CTR feature engineering",
    ]


def test_llm_workflow_resume_loads_completed_prefix():
    service = EvaluationService()
    trace_path = Path("data/runtime/test_llm_resume_prefix.jsonl")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "type": "llm_workflow_case_result",
            "case_result": {
                "name": "case_a",
                "status": "completed",
                "case_passed": True,
                "stage_trace": [{"stage": "case", "status": "completed"}],
            },
        },
        {
            "type": "llm_workflow_case_result",
            "case_result": {
                "name": "case_b",
                "status": "completed",
                "case_passed": False,
                "stage_trace": [{"stage": "case", "status": "completed"}],
            },
        },
    ]
    try:
        trace_path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
        selected = [{"name": "case_a"}, {"name": "case_b"}, {"name": "case_c"}]

        loaded = service._load_resumable_llm_results(trace_path, selected)

        assert [item["name"] for item in loaded] == ["case_a", "case_b"]
        assert loaded[1]["case_passed"] is False
    finally:
        trace_path.unlink(missing_ok=True)


def test_llm_workflow_resume_stops_at_first_missing_case():
    service = EvaluationService()
    trace_path = Path("data/runtime/test_llm_resume_missing.jsonl")
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        trace_path.write_text(
            json.dumps(
                {
                    "type": "llm_workflow_case_result",
                    "case_result": {"name": "case_b", "status": "completed", "case_passed": True},
                }
            ),
            encoding="utf-8",
        )

        loaded = service._load_resumable_llm_results(trace_path, [{"name": "case_a"}, {"name": "case_b"}])

        assert loaded == []
    finally:
        trace_path.unlink(missing_ok=True)
