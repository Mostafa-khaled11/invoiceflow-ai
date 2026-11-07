"""FastAPI dependency wiring shared by InvoiceFlow route modules."""

from collections.abc import Generator, Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_db_session
from app.services.invoice_llm import GroqInvoiceLLMProvider, InvoiceLLMProvider
from app.services.invoice_storage import InvoiceStorage
from app.services.oidc import OIDCClient


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a request-scoped SQLAlchemy session built from app settings."""

    settings: Settings = request.app.state.settings
    yield from get_db_session(settings)


def get_invoice_storage(request: Request) -> InvoiceStorage:
    """Build the private invoice storage service for the current request."""

    settings: Settings = request.app.state.settings
    return InvoiceStorage(settings.invoice_storage_path)


def get_app_settings(request: Request) -> Settings:
    """Return the request application's immutable settings object."""

    return request.app.state.settings


def get_invoice_llm_provider(request: Request) -> InvoiceLLMProvider:
    """Return the configured server-side provider; never expose its secret."""

    settings: Settings = request.app.state.settings
    if settings.llm_provider != "groq":
        raise RuntimeError("Unsupported LLM provider configuration")
    return GroqInvoiceLLMProvider(settings)


def get_oidc_client(request: Request) -> Iterator[OIDCClient]:
    """Return the configured OIDC client; tests may override this dependency."""

    client = OIDCClient(request.app.state.settings)
    try:
        yield client
    finally:
        client.close()
