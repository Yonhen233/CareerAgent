from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CareerAgent"
    app_version: str = "1.0.0"
    app_env: str = "development"

    database_url: str = "sqlite:///./data/career_agent.db"

    llm_api_key: str | None = None
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_routing_enabled: bool = True
    llm_flash_model: str = "deepseek-v4-flash"
    llm_pro_model: str = "deepseek-v4-pro"
    llm_flash_trace_prefixes: str = (
        "natural_language.,resume_parser.,jd_parser.,evaluation.llm_judge_suitability,"
        "resume_tailor.,application."
    )
    llm_pro_trace_prefixes: str = (
        "resume_review.,interview_prep.,interview_agentic_rag.,evaluation.interview_claim_verifier."
    )
    llm_flash_max_tokens_multiplier: float = Field(default=1.15, ge=1.0, le=1.5)
    llm_timeout_seconds: float = 120.0
    llm_retry_attempts: int = 1
    llm_retry_backoff_seconds: float = 0.75
    llm_fallback_enabled: bool = False
    llm_thinking_mode: str = "auto"
    llm_reasoning_effort: str = "high"
    llm_context_compression_enabled: bool = True
    llm_context_max_chars: int = 9000
    llm_evidence_max_chars: int = 3600
    context_runtime_v2_enabled: bool = True
    context_runtime_v2_shadow_mode: bool = False
    context_management_v3_enabled: bool = True
    context_model_window_tokens: int = Field(default=65536, ge=4096, le=1000000)
    context_token_soft_limit_ratio: float = Field(default=0.70, ge=0.30, le=0.90)
    context_token_high_limit_ratio: float = Field(default=0.85, ge=0.50, le=0.97)
    context_token_hard_limit_ratio: float = Field(default=0.95, ge=0.60, le=1.0)
    context_output_reserve_tokens: int = Field(default=4096, ge=256, le=100000)
    context_safety_margin_tokens: int = Field(default=1024, ge=128, le=32000)
    context_tool_schema_reserve_tokens: int = Field(default=1024, ge=0, le=32000)
    context_jit_max_calls: int = Field(default=3, ge=0, le=20)
    context_jit_max_tokens_per_call: int = Field(default=1600, ge=128, le=16000)
    context_compaction_enabled: bool = True
    context_cache_enabled: bool = True
    context_cache_max_entries: int = Field(default=512, ge=16, le=10000)
    context_tokenizer_model: str | None = None
    conversation_recent_turns: int = Field(default=3, ge=1, le=10)
    conversation_compaction_budget_ratio: float = Field(default=0.25, ge=0.1, le=0.6)
    conversation_compactor_max_tokens: int = Field(default=1200, ge=256, le=4000)
    parser_document_batch_chars: int = Field(default=16000, ge=4000, le=60000)
    token_optimization_v2_enabled: bool = True
    token_optimization_shadow_mode: bool = False
    dynamic_tool_catalog_enabled: bool = True
    batch_tool_calls_enabled: bool = True
    parallel_tool_calls_enabled: bool = True
    tool_result_artifact_enabled: bool = True
    delta_context_enabled: bool = True
    llm_max_calls_per_run: int = Field(default=12, ge=1, le=200)
    llm_max_attempts_per_run: int = Field(default=18, ge=1, le=400)
    llm_max_repair_calls: int = Field(default=2, ge=0, le=20)
    llm_max_input_tokens_per_run: int = Field(default=120000, ge=1000, le=4000000)
    llm_max_output_tokens_per_run: int = Field(default=40000, ge=256, le=1000000)
    llm_max_total_tokens_per_run: int = Field(default=160000, ge=1256, le=5000000)
    interview_rag_max_questions: int = 10
    interview_rag_answer_batch_size: int = 10
    interview_rag_llm_concurrency: int = 2
    interview_rag_verify_question_batch_size: int = 10
    interview_rag_verify_max_tokens: int = 2800
    interview_rag_json_repair_attempts: int = 1
    interview_rag_retrieval_top_n: int = 20
    interview_rag_evidence_top_k: int = 5
    interview_rag_evidence_chars: int = 360
    interview_rag_rrf_k: int = 60
    interview_rag_min_answer_chars: int = 120
    interview_rag_answer_repair_attempts: int = 2
    interview_rag_max_llm_calls: int = 8
    interview_rag_max_prompt_chars: int = 100000
    interview_rag_max_completion_tokens: int = 15000

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    api_url: str | None = None
    base_url: str | None = None
    admin_api_key: str | None = None
    require_admin_for_mutations: bool = False

    upload_dir: str = "data/uploads"
    export_dir: str = "data/exports"
    chroma_dir: str = "data/chroma"
    langgraph_checkpoint_file: str = "data/runtime/langgraph_checkpoints.sqlite"
    langgraph_checkpoint_backend: str = "sqlite"
    langgraph_checkpoint_postgres_dsn: str | None = None
    redis_enabled: bool = False
    redis_mode: str = "standalone"
    redis_url: str = "redis://localhost:6379/0"
    redis_sentinel_urls: str = "redis://localhost:26379"
    redis_sentinel_master_name: str = "mymaster"
    redis_socket_timeout_seconds: float = 15.0
    redis_queue_name: str = "career_agent:runs"
    redis_high_priority_queue_name: str = "career_agent:runs:high"
    redis_low_priority_queue_name: str = "career_agent:runs:low"
    redis_dead_letter_queue_name: str = "career_agent:runs:dead_letter"
    redis_run_lock_ttl_seconds: int = 1800
    redis_heartbeat_ttl_seconds: int = 300
    redis_rate_limit_window_seconds: int = 60
    redis_rate_limit_max_runs: int = 10
    redis_worker_max_attempts: int = 3
    redis_queued_recovery_after_minutes: int = 5
    redis_worker_concurrency: int = 2
    redis_worker_poll_timeout_seconds: int = 10
    redis_worker_recovery_interval_seconds: int = 60
    agent_run_stale_after_minutes: int = 30
    agent_run_max_recovery_attempts: int = 3
    agent_active_run_limit_per_profile: int = 3
    agent_max_tool_steps: int = Field(default=48, ge=4, le=200)
    agent_max_identical_tool_calls: int = Field(default=2, ge=1, le=10)
    agent_max_no_progress_cycles: int = Field(default=2, ge=1, le=10)
    agent_strict_tool_contracts: bool = True
    agent_tool_retry_backoff_seconds: float = Field(default=0.4, ge=0.0, le=10.0)
    agent_tool_circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    agent_tool_circuit_cooldown_seconds: int = Field(default=60, ge=1, le=3600)
    agent_online_quality_min_score: float = Field(default=0.75, ge=0.0, le=1.0)
    agent_memory_context_max_items: int = Field(default=12, ge=1, le=50)
    agent_memory_context_max_chars: int = Field(default=1600, ge=200, le=10000)
    natural_agent_max_llm_calls: int = Field(default=12, ge=1, le=100)
    natural_agent_max_prompt_chars: int = Field(default=140000, ge=1000, le=2000000)
    natural_agent_max_completion_tokens: int = Field(default=32000, ge=1000, le=500000)
    diagnostic_redact_pii: bool = True
    rbac_enabled: bool = False
    rbac_trusted_header_auth: bool = True
    rbac_default_tenant_id: str = "default"
    rbac_admin_roles: str = "owner,admin,ops"
    session_secret_key: str = "dev-change-me"
    session_cookie_name: str = "careeragent_session"
    session_ttl_seconds: int = 28800
    session_bootstrap_admin_email: str | None = None
    session_bootstrap_admin_password: str | None = None
    session_password_iterations: int = 120000
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_from_email: str | None = None
    outbound_email_draft_dir: str = "data/exports/email_drafts"
    browser_apply_headless: bool = True
    browser_apply_timeout_ms: int = 30000
    supervisor_health_file: str = "data/runtime/worker_supervisor_health.json"
    supervisor_drain_file: str = "data/runtime/worker_supervisor.drain"
    supervisor_log_json: bool = True
    prompt_injection_classifier_enabled: bool = True
    prompt_injection_classifier_threshold: float = 0.72
    chunk_size: int = 900
    chunk_overlap: int = 160
    pdf_max_upload_mb: int = Field(default=15, ge=1, le=100)
    pdf_max_pages: int = Field(default=30, ge=1, le=200)
    pdf_min_text_chars_per_page: int = Field(default=24, ge=1, le=500)
    pdf_min_printable_ratio: float = Field(default=0.75, ge=0.0, le=1.0)
    pdf_min_alnum_ratio: float = Field(default=0.35, ge=0.0, le=1.0)
    pdf_max_replacement_ratio: float = Field(default=0.01, ge=0.0, le=1.0)
    pdf_ocr_enabled: bool = True
    pdf_ocr_dpi: int = Field(default=200, ge=96, le=400)
    pdf_ocr_min_confidence: float = Field(default=0.45, ge=0.0, le=1.0)
    pdf_max_render_pixels: int = Field(default=20_000_000, ge=1_000_000, le=100_000_000)
    pdf_cross_page_tail_chars: int = Field(default=260, ge=80, le=1000)
    pdf_cross_page_head_chars: int = Field(default=520, ge=120, le=1600)
    embedding_dimensions: int = 256
    embedding_provider: str = "sentence_transformers"
    embedding_model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_cache_dir: str = "data/models"
    embedding_batch_size: int = 32
    embedding_normalize: bool = True
    embedding_provider_fallback: str = "error"
    vector_backend: str = "hybrid"
    retrieval_vector_weight: float = 0.45
    retrieval_lexical_weight: float = 0.50
    retrieval_type_boost: float = 0.05
    reranker_enabled: bool = True
    reranker_provider: str = "cross_encoder"
    reranker_model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_top_n: int = 20
    reranker_batch_size: int = 16
    reranker_score_weight: float = 0.30
    reranker_promotion_gap: float = 0.02
    reranker_anchor_top_n: int = 5
    reranker_provider_fallback: str = "error"
    rag_multi_query_enabled: bool = True
    rag_multi_query_rrf_k: int = Field(default=60, ge=1, le=200)
    rag_min_evidence_chunks: int = Field(default=1, ge=1, le=20)
    rag_min_vector_score: float = Field(default=0.50, ge=-1.0, le=1.0)
    rag_min_query_coverage: float = Field(default=0.10, ge=0.0, le=1.0)
    rag_min_first_stage_score: float = Field(default=0.45, ge=-1.0, le=1.0)
    rag_hash_min_vector_score: float = Field(default=0.28, ge=-1.0, le=1.0)
    rag_hash_min_first_stage_score: float = Field(default=0.30, ge=-1.0, le=1.0)
    job_ingest_concurrency: int = 6

    job_search_timeout_seconds: float = 18.0
    user_agent: str = Field(
        default=(
            "CareerAgent/1.0 "
            "(resume-job matching assistant; respectful public career-site crawler)"
        )
    )
    tencent_careers_enabled: bool = True
    baidu_careers_enabled: bool = True
    meituan_careers_enabled: bool = True
    bytedance_careers_enabled: bool = True
    alibaba_careers_enabled: bool = True
    jd_careers_enabled: bool = True
    china_telecom_careers_enabled: bool = True
    huawei_careers_enabled: bool = True
    iflytek_careers_enabled: bool = True
    tcl_careers_enabled: bool = True
    midea_careers_enabled: bool = True
    xiaomi_careers_enabled: bool = True
    oppo_careers_enabled: bool = True
    skyworth_careers_enabled: bool = True
    wind_careers_enabled: bool = True
    moka_china_careers_enabled: bool = True
    didi_careers_enabled: bool = True
    honor_careers_enabled: bool = True
    kuaishou_careers_enabled: bool = True
    lenovo_careers_enabled: bool = True
    vivo_careers_enabled: bool = True
    netease_careers_enabled: bool = True
    xiaohongshu_careers_enabled: bool = True
    bilibili_careers_enabled: bool = True
    antgroup_careers_enabled: bool = True
    qihu360_careers_enabled: bool = True
    dewu_careers_enabled: bool = True
    minimax_careers_enabled: bool = True
    zhipu_careers_enabled: bool = True
    job_source_browser_headless: bool = True
    job_source_browser_timeout_ms: int = 30000
    lever_careers_enabled: bool = False
    lever_company_slugs: str = "anthropic,cohere,scaleai,perplexityai"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def base_path(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def upload_path(self) -> Path:
        return self.base_path / self.upload_dir

    @property
    def export_path(self) -> Path:
        return self.base_path / self.export_dir

    @property
    def chroma_path(self) -> Path:
        return self.base_path / self.chroma_dir

    @property
    def langgraph_checkpoint_path(self) -> Path:
        return self.base_path / self.langgraph_checkpoint_file

    @property
    def embedding_cache_path(self) -> Path:
        return self.base_path / self.embedding_cache_dir

    @property
    def effective_llm_api_key(self) -> str | None:
        return self.llm_api_key or self.openai_api_key

    @property
    def effective_llm_base_url(self) -> str:
        explicit_compatible_url = self.openai_base_url or self.base_url or self.api_url
        if explicit_compatible_url and self.llm_base_url == Settings.model_fields["llm_base_url"].default:
            return explicit_compatible_url
        return self.llm_base_url or explicit_compatible_url or ""

    @property
    def llm_flash_trace_prefix_list(self) -> list[str]:
        return [item.strip() for item in self.llm_flash_trace_prefixes.split(",") if item.strip()]

    @property
    def llm_pro_trace_prefix_list(self) -> list[str]:
        return [item.strip() for item in self.llm_pro_trace_prefixes.split(",") if item.strip()]

    @property
    def lever_slugs(self) -> list[str]:
        return [slug.strip() for slug in self.lever_company_slugs.split(",") if slug.strip()]

    @property
    def redis_sentinel_endpoints(self) -> list[tuple[str, int]]:
        endpoints: list[tuple[str, int]] = []
        for raw in self.redis_sentinel_urls.split(","):
            value = raw.strip()
            if not value:
                continue
            if "://" in value:
                value = value.split("://", 1)[1]
            host, _, port = value.partition(":")
            endpoints.append((host, int(port or 26379)))
        return endpoints

    @property
    def redis_queue_names_by_priority(self) -> dict[str, str]:
        return {
            "high": self.redis_high_priority_queue_name,
            "normal": self.redis_queue_name,
            "low": self.redis_low_priority_queue_name,
        }

    @property
    def redis_priority_queue_names(self) -> list[str]:
        return [
            self.redis_high_priority_queue_name,
            self.redis_queue_name,
            self.redis_low_priority_queue_name,
        ]

    @property
    def rbac_admin_role_set(self) -> set[str]:
        return {role.strip() for role in self.rbac_admin_roles.split(",") if role.strip()}

    @property
    def outbound_email_draft_path(self) -> Path:
        return self.base_path / self.outbound_email_draft_dir

    @property
    def supervisor_health_path(self) -> Path:
        return self.base_path / self.supervisor_health_file

    @property
    def supervisor_drain_path(self) -> Path:
        return self.base_path / self.supervisor_drain_file


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
