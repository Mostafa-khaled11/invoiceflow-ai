"""OIDC login and server-side browser session endpoints."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_db, get_oidc_client
from app.core.config import Settings
from app.models.identity import UserAccountStatus
from app.services.authentication import (
    AuthenticationConfigurationError,
    AuthenticationCookieError,
    create_authenticated_session,
    csrf_token_for_session,
    generate_pkce_verifier,
    load_authenticated_session,
    open_login_transaction,
    pkce_challenge,
    seal_login_transaction,
    validate_auth_configuration,
    validate_csrf,
    validate_request_origin,
)
from app.services.identity_persistence import (
    consume_oidc_login_transaction,
    create_oidc_login_transaction,
    find_oidc_identity,
    revoke_session,
)
from app.services.oidc import OIDCClient, OIDCError
from app.services.security_audit import record_security_audit_event

router = APIRouter(prefix="/auth")


def _samesite(settings: Settings) -> Literal["lax", "strict"]:
    """Return the validated cookie SameSite policy with a precise type."""

    value = settings.auth_cookie_samesite.lower()
    if value not in {"lax", "strict"}:
        raise RuntimeError("Invalid authentication cookie SameSite policy")
    return cast(Literal["lax", "strict"], value)


def _require_oidc(settings: Settings) -> None:
    try:
        validate_auth_configuration(settings)
    except AuthenticationConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC authentication is not available",
        ) from exc
    if not settings.oidc_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC authentication is disabled",
        )


def _set_transaction_cookie(response: Response, settings: Settings, value: str) -> None:
    response.set_cookie(
        key=settings.auth_transaction_cookie_name,
        value=value,
        max_age=settings.auth_state_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=_samesite(settings),
        path=f"{settings.api_v1_prefix}/auth/callback",
    )


def _set_session_cookie(response: Response, settings: Settings, value: str) -> None:
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=value,
        max_age=settings.auth_session_absolute_hours * 60 * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=_samesite(settings),
        path="/",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_session_cookie_name,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=_samesite(settings),
        path="/",
    )


def _clear_transaction_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_transaction_cookie_name,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=_samesite(settings),
        path=f"{settings.api_v1_prefix}/auth/callback",
    )


def _account_payload(account) -> dict[str, str | None]:
    return {
        "id": str(account.id),
        "status": account.status,
        "email": account.email,
        "display_name": account.display_name,
    }


def _record_auth_failure(session: Session, category: str) -> None:
    """Persist a sanitized anonymous authentication failure in its own unit."""

    session.rollback()
    record_security_audit_event(
        session,
        event_type="auth.login_failed",
        actor_type="system",
        actor_id="anonymous",
        metadata={"failure_category": category},
    )
    session.commit()


@router.get("/login")
def login(
    settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[Session, Depends(get_db)],
    oidc: Annotated[OIDCClient, Depends(get_oidc_client)],
) -> RedirectResponse:
    """Start one OIDC Authorization Code + PKCE transaction."""

    _require_oidc(settings)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = generate_pkce_verifier()
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.auth_state_ttl_seconds)
    transaction_cookie = seal_login_transaction(
        settings,
        state=state,
        nonce=nonce,
        code_verifier=verifier,
        expires_at=expires_at,
    )
    try:
        authorization_url = oidc.authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=pkce_challenge(verifier),
        )
        create_oidc_login_transaction(
            session,
            state=state,
            nonce=nonce,
            redirect_uri=settings.oidc_redirect_uri or "",
            expires_at=expires_at,
            created_at=now,
        )
        record_security_audit_event(
            session,
            event_type="auth.login_started",
            actor_type="system",
            actor_id="anonymous",
            metadata={"auth_method": "oidc"},
        )
        session.commit()
    except OIDCError as exc:
        session.rollback()
        _record_auth_failure(session, "provider_unavailable")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OIDC provider is unavailable",
        ) from exc
    except Exception:
        session.rollback()
        raise

    response = RedirectResponse(
        authorization_url,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    _set_transaction_cookie(
        response,
        settings,
        transaction_cookie,
    )
    return response


@router.get("/callback")
def callback(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[Session, Depends(get_db)],
    oidc: Annotated[OIDCClient, Depends(get_oidc_client)],
) -> JSONResponse:
    """Finish OIDC login after validating state, nonce, token, and local mapping."""

    _require_oidc(settings)
    provider_error = request.query_params.get("error")
    if provider_error:
        _record_auth_failure(session, "provider_denied")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC authorization was not completed",
        )
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        _record_auth_failure(session, "missing_callback_parameters")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC callback is missing required parameters",
        )
    cookie_value = request.cookies.get(settings.auth_transaction_cookie_name)
    if not cookie_value:
        _record_auth_failure(session, "missing_transaction_cookie")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC transaction is missing or expired",
        )
    try:
        transaction_cookie = open_login_transaction(settings, cookie_value)
    except AuthenticationCookieError as exc:
        _record_auth_failure(session, "invalid_transaction_cookie")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC transaction is missing or expired",
        ) from exc
    if not secrets.compare_digest(transaction_cookie.state, state):
        _record_auth_failure(session, "state_mismatch")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC callback state is invalid",
        )
    transaction = consume_oidc_login_transaction(
        session,
        state=state,
        nonce=transaction_cookie.nonce,
    )
    if transaction is None:
        _record_auth_failure(session, "transaction_unavailable")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC transaction is missing, expired, or already used",
        )
    if transaction.redirect_uri != settings.oidc_redirect_uri:
        _record_auth_failure(session, "redirect_uri_mismatch")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC redirect transaction is invalid",
        )
    session.commit()
    try:
        id_token = oidc.exchange_code(
            code=code,
            code_verifier=transaction_cookie.code_verifier,
        )
        verified = oidc.validate_id_token(
            id_token=id_token,
            nonce=transaction_cookie.nonce,
        )
    except OIDCError as exc:
        _record_auth_failure(session, "identity_validation_failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OIDC identity validation failed",
        ) from exc

    identity = find_oidc_identity(
        session,
        issuer=verified.issuer,
        subject=verified.subject,
    )
    if identity is None:
        _record_auth_failure(session, "identity_not_approved")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OIDC identity is not approved for this application",
        )
    account = identity.user
    if account.status != UserAccountStatus.ACTIVE.value:
        _record_auth_failure(session, "account_inactive")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local account is not active",
        )
    if verified.email is not None:
        account.email = verified.email
        identity.last_seen_email = verified.email
    if verified.display_name is not None:
        account.display_name = verified.display_name
    identity.email_verified = verified.email_verified
    raw_session_token, _ = create_authenticated_session(
        session,
        settings,
        user_id=account.id,
    )
    record_security_audit_event(
        session,
        event_type="auth.login_succeeded",
        actor_type="human",
        actor_id=str(account.id),
        target_type="user_account",
        target_id=account.id,
        metadata={"auth_method": "oidc"},
    )
    session.commit()

    response = JSONResponse(
        {
            "authenticated": True,
            "account": _account_payload(account),
        }
    )
    _set_session_cookie(response, settings, raw_session_token)
    _clear_transaction_cookie(response, settings)
    return response


@router.get("/session")
def current_session(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[Session, Depends(get_db)],
) -> JSONResponse:
    """Return safe local account metadata and a session-bound CSRF token."""

    authenticated = load_authenticated_session(session, request, settings)
    if authenticated is None:
        session.commit()
        response = JSONResponse(
            {"authenticated": False}, status_code=status.HTTP_401_UNAUTHORIZED
        )
        _clear_session_cookie(response, settings)
        return response
    session.commit()
    return JSONResponse(
        {
            "authenticated": True,
            "account": _account_payload(authenticated.account),
            "csrf_token": csrf_token_for_session(settings, authenticated.raw_token),
        }
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    session: Annotated[Session, Depends(get_db)],
) -> Response:
    """Revoke the current session after origin and CSRF validation."""

    if not validate_request_origin(request, settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is not trusted",
        )
    authenticated = load_authenticated_session(session, request, settings)
    if authenticated is None:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required",
        )
    if not validate_csrf(
        settings,
        authenticated.raw_token,
        request.headers.get("x-csrf-token"),
    ):
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )
    revoke_session(
        session,
        session_id=authenticated.record.id,
        reason="user logout",
    )
    record_security_audit_event(
        session,
        event_type="auth.logout",
        actor_type="human",
        actor_id=str(authenticated.account.id),
        target_type="session",
        target_id=authenticated.record.id,
        metadata={},
    )
    session.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(response, settings)
    return response
