"""InvoiceFlow AI FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as api_v1_router
from app.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application with injectable settings for tests."""

    resolved_settings = settings or get_settings()
    application = FastAPI(
        title="InvoiceFlow AI API",
        version="0.1.0",
        description="Foundation API for InvoiceFlow AI.",
    )
    application.state.settings = resolved_settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    application.include_router(
        api_v1_router,
        prefix=resolved_settings.api_v1_prefix,
    )
    return application


app = create_app()
