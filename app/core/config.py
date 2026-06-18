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
    llm_base_url: str = "https://llmapi.paratera.com"
    llm_model: str = "DeepSeek-V4-Pro"
    llm_timeout_seconds: float = 120.0
    llm_retry_attempts: int = 1
    llm_retry_backoff_seconds: float = 0.75
    llm_fallback_enabled: bool = False
    llm_thinking_mode: str = "auto"
    llm_reasoning_effort: str = "high"
    llm_context_compression_enabled: bool = True
    llm_context_max_chars: int = 9000
    llm_evidence_max_chars: int = 3600

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
    def lever_slugs(self) -> list[str]:
        return [slug.strip() for slug in self.lever_company_slugs.split(",") if slug.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
