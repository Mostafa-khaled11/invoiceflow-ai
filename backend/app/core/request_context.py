"""Trusted, request-scoped HTTP request and correlation context."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import UUID, uuid4

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_CORRELATION_ID_LENGTH = 128
_request_context: ContextVar[RequestContext | None] = ContextVar(
    "invoiceflow_request_context", default=None
)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Immutable server-owned identifiers for one HTTP request."""

    request_id: UUID
    correlation_id: UUID


def get_request_context() -> RequestContext | None:
    """Return the current request context, if called inside an HTTP request."""

    return _request_context.get()


def _validated_correlation_id(raw_value: str | None) -> UUID:
    """Validate a client correlation UUID or generate a safe replacement.

    Invalid external values are deliberately not echoed or used for identity;
    replacing them preserves existing endpoint behavior while maintaining a
    server-generated trace identifier.
    """

    if raw_value is None or len(raw_value) > MAX_CORRELATION_ID_LENGTH:
        return uuid4()
    try:
        return UUID(raw_value)
    except ValueError:
        return uuid4()


def _correlation_id_from_scope(scope: Scope) -> UUID:
    """Resolve exactly one raw correlation header without trusting duplicates."""

    values = [
        value
        for name, value in scope.get("headers", [])
        if name.lower() == b"x-correlation-id"
    ]
    if len(values) != 1:
        return uuid4()
    return _validated_correlation_id(values[0].decode("latin-1"))


class RequestContextMiddleware:
    """Install and clean up trusted request/correlation context per HTTP call."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        context = RequestContext(
            request_id=uuid4(),
            correlation_id=_correlation_id_from_scope(scope),
        )
        token: Token[RequestContext | None] = _request_context.set(context)
        request.state.request_context = context

        async def send_with_context(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = str(context.request_id)
                headers["X-Correlation-ID"] = str(context.correlation_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            request.state.request_context = None
            _request_context.reset(token)
