"""Environment-backed application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "invoiceflow-api"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str | None = None
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    invoice_storage_path: Path = Path("storage")
    """Private root for stored invoice PDFs; never exposed via static routes."""
    max_invoice_pages: int = 50
    min_extracted_text_chars: int = 50
    llm_provider: str = "groq"
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    max_llm_input_chars: int = Field(default=20_000, gt=0)
    invoice_extraction_prompt_version: str = "invoice-extraction-v1"
    invoice_extraction_schema_version: str = "invoice-schema-v1"

    @property
    def cors_origin_list(self) -> list[str]:
        """Return comma-separated CORS origins as a normalized list."""

        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
