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
    llm_timeout_seconds: float = 60.0

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    api_url: str | None = None
    base_url: str | None = None

    upload_dir: str = "data/uploads"
    export_dir: str = "data/exports"
    chunk_size: int = 900
    chunk_overlap: int = 160
    embedding_dimensions: int = 256

    job_search_timeout_seconds: float = 18.0
    user_agent: str = Field(
        default=(
            "CareerAgent/1.0 "
            "(resume-job matching assistant; respectful public career-site crawler)"
        )
    )
    tencent_careers_enabled: bool = True
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
    def effective_llm_api_key(self) -> str | None:
        return self.llm_api_key or self.openai_api_key

    @property
    def effective_llm_base_url(self) -> str:
        return self.llm_base_url or self.openai_base_url or self.base_url or self.api_url or ""

    @property
    def lever_slugs(self) -> list[str]:
        return [slug.strip() for slug in self.lever_company_slugs.split(",") if slug.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
