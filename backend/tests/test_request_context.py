"""Offline tests for trusted request and correlation context."""

from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
import pytest
from app.core.config import Settings
from app.core.request_context import (
    MAX_CORRELATION_ID_LENGTH,
    RequestContextMiddleware,
    get_request_context,
)
from app.main import create_app
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware


def test_request_id_is_server_generated_and_returned(app_settings) -> None:
    with TestClient(create_app(app_settings)) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    request_id = UUID(response.headers["x-request-id"])
    correlation_id = UUID(response.headers["x-correlation-id"])
    assert request_id != correlation_id


def test_valid_correlation_id_is_normalized_and_accepted(app_settings) -> None:
    supplied = "{12345678-1234-5678-1234-567812345678}"
    with TestClient(create_app(app_settings)) as client:
        response = client.get(
            "/api/v1/health",
            headers={"X-Correlation-ID": supplied},
        )

    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == str(UUID(supplied))


@pytest.mark.parametrize(
    "value",
    [
        "not-a-uuid",
        "x" * (MAX_CORRELATION_ID_LENGTH + 1),
    ],
)
def test_invalid_correlation_id_is_replaced_without_changing_endpoint_behavior(
    app_settings, value: str
) -> None:
    with TestClient(create_app(app_settings)) as client:
        response = client.get(
            "/api/v1/health",
            headers={"X-Correlation-ID": value},
        )

    assert response.status_code == 200
    assert UUID(response.headers["x-correlation-id"])
    assert response.headers["x-correlation-id"] != value


@pytest.mark.parametrize(
    "values",
    [
        [str(UUID(int=1)), str(UUID(int=2))],
        ["not-a-uuid", str(UUID(int=2))],
    ],
)
def test_duplicate_correlation_headers_are_replaced(
    app_settings, values: list[str]
) -> None:
    with TestClient(create_app(app_settings)) as client:
        response = client.get(
            "/api/v1/health",
            headers=[("X-Correlation-ID", value) for value in values],
        )

    assert response.status_code == 200
    generated = UUID(response.headers["x-correlation-id"])
    assert str(generated) not in values


def test_response_headers_are_owned_and_unrelated_headers_preserved() -> None:
    application = FastAPI()

    @application.get("/headers")
    async def headers() -> Response:
        return Response(
            content="ok",
            headers={
                "X-Request-ID": "downstream-request",
                "X-Correlation-ID": "downstream-correlation",
                "X-Unrelated": "preserve-me",
            },
        )

    # Build the test app with the context middleware outermost, as production
    # does, after the route-level middleware has been registered.
    application.middleware_stack = RequestContextMiddleware(
        application.build_middleware_stack()
    )
    with TestClient(application) as client:
        response = client.get("/headers")

    assert response.status_code == 200
    assert UUID(response.headers["x-request-id"])
    assert UUID(response.headers["x-correlation-id"])
    assert response.headers["x-unrelated"] == "preserve-me"
    assert len(response.headers.get_list("x-request-id")) == 1
    assert len(response.headers.get_list("x-correlation-id")) == 1


async def _factory_marker_dispatch(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Factory-Middleware"] = "present"
    return response


def test_factory_remains_fastapi_compatible_and_add_middleware_is_lazy() -> None:
    application = create_app(Settings(database_url=None))
    assert isinstance(application, FastAPI)
    application.add_middleware(
        BaseHTTPMiddleware,
        dispatch=_factory_marker_dispatch,
    )

    with TestClient(application) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.headers["x-factory-middleware"] == "present"


def _production_error_app() -> FastAPI:
    application = create_app(Settings(database_url=None))
    application.add_api_route("/boom", lambda: 1 / 0, methods=["GET"])
    return application


def test_allowed_origin_unhandled_500_has_context_and_cors_headers() -> None:
    application = _production_error_app()
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(
            "/boom",
            headers={"Origin": "http://localhost:5173"},
        )

    assert response.status_code == 500
    assert len(response.headers.get_list("x-request-id")) == 1
    assert len(response.headers.get_list("x-correlation-id")) == 1
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "division by zero" not in response.text


def test_disallowed_origin_unhandled_500_has_no_cors_grant() -> None:
    application = _production_error_app()
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(
            "/boom",
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") is None
    assert len(response.headers.get_list("x-request-id")) == 1
    assert len(response.headers.get_list("x-correlation-id")) == 1


def test_allowed_origin_success_and_preflight_have_context_headers() -> None:
    application = create_app(Settings(database_url=None))
    with TestClient(application) as client:
        success = client.get(
            "/api/v1/health",
            headers={"Origin": "http://localhost:5173"},
        )
        preflight = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Correlation-ID",
            },
        )

    assert success.status_code == 200
    assert success.headers["access-control-allow-origin"] == ("http://localhost:5173")
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == ("http://localhost:5173")
    for response in (success, preflight):
        assert len(response.headers.get_list("x-request-id")) == 1
        assert len(response.headers.get_list("x-correlation-id")) == 1


def test_outer_composition_has_one_context_and_one_cors_instance() -> None:
    application = create_app(Settings(database_url=None))
    assert (
        sum(
            middleware.cls is RequestContextMiddleware
            for middleware in application.user_middleware
        )
        == 0
    )
    assert (
        sum(
            middleware.cls is CORSMiddleware
            for middleware in application.user_middleware
        )
        == 0
    )

    with TestClient(application) as client:
        client.get("/api/v1/health")

    outer = application._invoiceflow_outer_stack
    assert isinstance(outer, RequestContextMiddleware)
    assert outer.app.__class__.__name__ == "CORSMiddleware"


def _context_test_app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(RequestContextMiddleware)

    @application.get("/context")
    async def context(request: Request) -> dict[str, str | None]:
        current = get_request_context()
        state_context = request.state.request_context
        await asyncio.sleep(0)
        return {
            "request_id": str(current.request_id) if current else None,
            "correlation_id": str(current.correlation_id) if current else None,
            "same_state": str(current is state_context),
            "has_actor": str(hasattr(current, "actor_id")),
        }

    @application.get("/error")
    async def error() -> None:
        raise RuntimeError("expected test exception")

    @application.get("/cancel")
    async def cancel(request: Request) -> None:
        request.app.state.cancel_request_state = request.state
        request.app.state.cancel_entered.set()
        await asyncio.Event().wait()

    return application


def _outer_context_error_app() -> FastAPI:
    application = FastAPI()

    @application.get("/error")
    async def error() -> None:
        raise RuntimeError("expected test exception")

    application.middleware_stack = RequestContextMiddleware(
        application.build_middleware_stack()
    )
    return application


@pytest.mark.anyio
async def test_concurrent_requests_have_isolated_contexts() -> None:
    application = _context_test_app()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    @application.get("/overlap/{name}")
    async def overlap(name: str) -> dict[str, str]:
        before = get_request_context()
        if name == "first":
            first_entered.set()
            await release_first.wait()
        else:
            await first_entered.wait()
            release_first.set()
            await asyncio.sleep(0)
        after = get_request_context()
        assert before is not None and after is not None
        return {
            "before_request_id": str(before.request_id),
            "after_request_id": str(after.request_id),
            "before_correlation_id": str(before.correlation_id),
            "after_correlation_id": str(after.correlation_id),
        }

    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(
            client.get(
                "/overlap/first",
                headers={"X-Correlation-ID": str(UUID(int=1))},
            ),
            client.get(
                "/overlap/second",
                headers={"X-Correlation-ID": str(UUID(int=2))},
            ),
        )

    for response in responses:
        payload = response.json()
        assert payload["before_request_id"] == payload["after_request_id"]
        assert payload["before_correlation_id"] == payload["after_correlation_id"]
    assert {response.json()["after_correlation_id"] for response in responses} == {
        str(UUID(int=1)),
        str(UUID(int=2)),
    }


@pytest.mark.anyio
async def test_exception_path_cleans_context() -> None:
    application = _context_test_app()
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(RuntimeError, match="expected test exception"):
            await client.get("/error")

    assert get_request_context() is None


def test_uncaught_exception_response_contains_context_headers() -> None:
    application = _outer_context_error_app()
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get("/error")

    response = asyncio.run(request())
    assert response.status_code == 500
    assert UUID(response.headers["x-request-id"])
    assert UUID(response.headers["x-correlation-id"])
    assert "expected test exception" not in response.text


@pytest.mark.anyio
async def test_cancellation_cleans_state_and_does_not_swallow_cancelled_error() -> None:
    application = _context_test_app()
    application.state.cancel_entered = asyncio.Event()
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        task = asyncio.create_task(client.get("/cancel"))
        await application.state.cancel_entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert application.state.cancel_request_state.request_context is None
        response = await client.get("/context")

    assert response.status_code == 200
    assert response.json()["request_id"] is not None


def test_actor_spoofing_headers_do_not_authenticate(anonymous_client) -> None:
    response = anonymous_client.get(
        f"/api/v1/invoices/{UUID(int=1)}",
        headers={
            "X-Actor-ID": "attacker",
            "X-Actor-Type": "admin",
            "X-User-ID": "attacker",
            "X-Role": "Admin",
        },
    )

    assert response.status_code == 401
