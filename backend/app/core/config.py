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
    n8n_enabled: bool = False
    n8n_base_url: str | None = None
    n8n_webhook_secret: str | None = None
    n8n_service_name: str = "n8n-aging-review-monitor"
    n8n_service_token_hash: str | None = None
    n8n_timeout_seconds: float = Field(default=5.0, gt=0)
    aging_review_threshold_minutes: int = Field(default=60, ge=1)
    oidc_enabled: bool = False
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    oidc_scopes: str = "openid profile email"
    oidc_timeout_seconds: float = Field(default=10.0, gt=0)
    oidc_transaction_cookie_secret: str | None = None
    auth_csrf_secret: str | None = None
    auth_session_cookie_name: str = "invoiceflow_session"
    auth_transaction_cookie_name: str = "invoiceflow_oidc_transaction"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"
    auth_session_idle_minutes: int = Field(default=30, ge=1)
    auth_session_absolute_hours: int = Field(default=8, ge=1)
    auth_session_activity_update_seconds: int = Field(default=300, ge=0)
    auth_state_ttl_seconds: int = Field(default=600, ge=60)
    auth_trusted_origins: str = ""
    auth_require_origin: bool = False
    auth_protection_complete: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        """Return comma-separated CORS origins as a normalized list."""

        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]

    @property
    def trusted_origin_list(self) -> list[str]:
        """Return explicit auth origins, falling back to configured CORS origins."""

        configured = self.auth_trusted_origins or self.cors_origins
        return [
            origin.strip().rstrip("/")
            for origin in configured.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()
