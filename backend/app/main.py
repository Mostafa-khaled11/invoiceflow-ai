"""InvoiceFlow AI FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.v1.router import router as api_v1_router
from app.core.config import Settings, get_settings
from app.core.request_context import RequestContextMiddleware
from app.services.authentication import validate_auth_configuration


class InvoiceFlowApplication(FastAPI):
    """FastAPI application with lazy outer request and CORS composition."""

    def __init__(self, *, cors_origins: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self._invoiceflow_cors_origins = cors_origins
        self._invoiceflow_outer_stack: ASGIApp | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run request context and CORS outside FastAPI's error boundary."""

        if self._invoiceflow_outer_stack is None:
            self._invoiceflow_outer_stack = RequestContextMiddleware(
                CORSMiddleware(
                    super().__call__,
                    allow_origins=self._invoiceflow_cors_origins,
                    allow_credentials=True,
                    allow_methods=["GET", "POST"],
                    allow_headers=["*"],
                )
            )
        await self._invoiceflow_outer_stack(scope, receive, send)


def create_app(settings: Settings | None = None) -> InvoiceFlowApplication:
    """Create the FastAPI application with injectable settings for tests."""

    resolved_settings = settings or get_settings()
    if resolved_settings.environment.lower() in {"production", "prod"}:
        validate_auth_configuration(resolved_settings)
    application = InvoiceFlowApplication(
        cors_origins=resolved_settings.cors_origin_list,
        title="InvoiceFlow AI API",
        version="0.1.0",
        description="Foundation API for InvoiceFlow AI.",
    )
    application.state.settings = resolved_settings
    application.include_router(
        api_v1_router,
        prefix=resolved_settings.api_v1_prefix,
    )
    return application


app = create_app()
