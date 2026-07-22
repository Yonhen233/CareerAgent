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
    llm_pro_trace_prefixes: str = "resume_review.,interview_prep.,interview_agentic_rag."
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
    interview_rag_max_questions: int = 10
    interview_rag_answer_batch_size: int = 10
    interview_rag_llm_concurrency: int = 2
    interview_rag_verify_question_batch_size: int = 10
    interview_rag_verify_max_tokens: int = 2800
    interview_rag_json_repair_attempts: int = 0
    interview_rag_retrieval_top_n: int = 20
    interview_rag_evidence_top_k: int = 5
    interview_rag_evidence_chars: int = 360
    interview_rag_rrf_k: int = 60
    interview_rag_min_answer_chars: int = 120
    interview_rag_answer_repair_attempts: int = 1
    interview_rag_max_llm_calls: int = 5
    interview_rag_max_prompt_chars: int = 60000
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
    agent_active_run_limit_per_profile: int = 3
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
