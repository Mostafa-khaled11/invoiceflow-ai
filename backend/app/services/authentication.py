"""Browser authentication helpers shared by the OIDC API routes.

This module owns protocol state, cookie protection, CSRF validation, and the
server-side session boundary. It deliberately does not enforce application
roles; that is a later authorization milestone.
"""

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.identity import UserAccount, UserAccountStatus, UserSession
from app.services.identity_persistence import (
    create_session,
    find_session_by_hash,
    hash_session_token,
    is_session_active,
    revoke_session,
    touch_session,
)

_AUTHORIZATION_ENFORCEMENT_IMPLEMENTED = False


class AuthenticationConfigurationError(RuntimeError):
    """Raised when authentication cannot safely operate in this environment."""


class AuthenticationCookieError(ValueError):
    """Raised when a protected browser transaction cookie is invalid."""


@dataclass(frozen=True)
class LoginTransactionPayload:
    """Short-lived values needed to finish one authorization-code flow."""

    state: str
    nonce: str
    code_verifier: str
    expires_at: int


@dataclass(frozen=True)
class AuthenticatedSession:
    """The active local account and its server-side session record."""

    raw_token: str
    record: UserSession
    account: UserAccount


def validate_auth_configuration(settings: Settings) -> None:
    """Fail closed for enabled authentication and production deployments."""

    production = settings.environment.lower() in {"production", "prod"}
    if production and not _AUTHORIZATION_ENFORCEMENT_IMPLEMENTED:
        raise AuthenticationConfigurationError(
            "Production deployment is blocked until PR-01C authorization enforcement"
        )
    if production and not settings.auth_protection_complete:
        raise AuthenticationConfigurationError(
            "Production authentication protection is not marked complete"
        )
    if not settings.oidc_enabled:
        if production:
            raise AuthenticationConfigurationError("OIDC authentication is disabled")
        return

    required = {
        "OIDC_ISSUER": settings.oidc_issuer,
        "OIDC_CLIENT_ID": settings.oidc_client_id,
        "OIDC_CLIENT_SECRET": settings.oidc_client_secret,
        "OIDC_REDIRECT_URI": settings.oidc_redirect_uri,
        "OIDC_TRANSACTION_COOKIE_SECRET": settings.oidc_transaction_cookie_secret,
        "AUTH_CSRF_SECRET": settings.auth_csrf_secret,
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        raise AuthenticationConfigurationError(
            "OIDC authentication is missing required configuration"
        )
    if settings.auth_cookie_samesite.lower() not in {"lax", "strict"}:
        raise AuthenticationConfigurationError(
            "AUTH_COOKIE_SAMESITE must be lax or strict"
        )
    if not settings.auth_csrf_secret or len(settings.auth_csrf_secret) < 32:
        raise AuthenticationConfigurationError(
            "AUTH_CSRF_SECRET must contain at least 32 characters"
        )
    if "openid" not in set(settings.oidc_scopes.split()):
        raise AuthenticationConfigurationError("OIDC_SCOPES must include openid")
    redirect = urlparse(settings.oidc_redirect_uri or "")
    if (
        redirect.scheme not in {"http", "https"}
        or not redirect.netloc
        or redirect.fragment
    ):
        raise AuthenticationConfigurationError(
            "OIDC_REDIRECT_URI must be an absolute HTTP URL without a fragment"
        )
    if production:
        if not settings.auth_cookie_secure:
            raise AuthenticationConfigurationError(
                "Production authentication cookies must be Secure"
            )
        if not settings.auth_session_cookie_name.startswith("__Host-"):
            raise AuthenticationConfigurationError(
                "Production session cookies must use a __Host- name"
            )
        if not settings.auth_require_origin:
            raise AuthenticationConfigurationError(
                "Production authentication must require Origin validation"
            )
        for value in (
            settings.oidc_issuer,
            settings.oidc_redirect_uri,
            *settings.trusted_origin_list,
        ):
            parsed = urlparse(value or "")
            if (
                not value
                or parsed.scheme != "https"
                or not parsed.netloc
                or parsed.path not in {"", "/"}
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                raise AuthenticationConfigurationError(
                    "Production authentication URLs must use HTTPS"
                )
    _fernet(settings)


def generate_pkce_verifier() -> str:
    """Generate a high-entropy PKCE verifier."""

    return secrets.token_urlsafe(64)


def pkce_challenge(verifier: str) -> str:
    """Return the RFC 7636 S256 challenge for a verifier."""

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _fernet(settings: Settings) -> Fernet:
    secret = settings.oidc_transaction_cookie_secret
    if not secret:
        raise AuthenticationConfigurationError(
            "OIDC transaction cookie secret is not configured"
        )
    try:
        return Fernet(secret.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AuthenticationConfigurationError(
            "OIDC transaction cookie secret is invalid"
        ) from exc


def seal_login_transaction(
    settings: Settings,
    *,
    state: str,
    nonce: str,
    code_verifier: str,
    expires_at: datetime,
) -> str:
    """Encrypt state, nonce, and verifier for the callback-only cookie."""

    payload = {
        "state": state,
        "nonce": nonce,
        "code_verifier": code_verifier,
        "expires_at": int(expires_at.timestamp()),
    }
    return (
        _fernet(settings)
        .encrypt(json.dumps(payload, separators=(",", ":")).encode())
        .decode("ascii")
    )


def open_login_transaction(settings: Settings, value: str) -> LoginTransactionPayload:
    """Decrypt and validate a callback transaction without exposing its values."""

    try:
        raw = _fernet(settings).decrypt(value.encode("ascii"))
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        state = payload["state"]
        nonce = payload["nonce"]
        verifier = payload["code_verifier"]
        expires_at = payload["expires_at"]
        if not all(isinstance(item, str) and item for item in (state, nonce, verifier)):
            raise ValueError
        if not isinstance(expires_at, int) or expires_at <= int(
            datetime.now(UTC).timestamp()
        ):
            raise ValueError
    except (
        InvalidToken,
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise AuthenticationCookieError("Invalid or expired OIDC transaction") from exc
    return LoginTransactionPayload(
        state=state,
        nonce=nonce,
        code_verifier=verifier,
        expires_at=expires_at,
    )


def csrf_token_for_session(settings: Settings, raw_token: str) -> str:
    """Derive a non-persistent CSRF token bound to the session cookie."""

    if not settings.auth_csrf_secret:
        raise AuthenticationConfigurationError("AUTH_CSRF_SECRET is not configured")
    return hmac.new(
        settings.auth_csrf_secret.encode(),
        raw_token.encode(),
        hashlib.sha256,
    ).hexdigest()


def validate_csrf(settings: Settings, raw_token: str, supplied: str | None) -> bool:
    """Constant-time validation of a session-bound CSRF header."""

    if not supplied:
        return False
    return hmac.compare_digest(csrf_token_for_session(settings, raw_token), supplied)


def validate_request_origin(request: Request, settings: Settings) -> bool:
    """Validate browser Origin when present, or require it when configured."""

    origin = request.headers.get("origin")
    if not origin:
        return not settings.auth_require_origin
    return origin.rstrip("/") in settings.trusted_origin_list


def create_authenticated_session(
    session: Session,
    settings: Settings,
    *,
    user_id,
) -> tuple[str, UserSession]:
    """Create a random opaque browser token and persist only its hash."""

    now = datetime.now(UTC)
    raw_token = secrets.token_urlsafe(32)
    record = create_session(
        session,
        user_id=user_id,
        token_hash=hash_session_token(raw_token),
        created_at=now,
        last_activity_at=now,
        idle_expires_at=now + timedelta(minutes=settings.auth_session_idle_minutes),
        absolute_expires_at=now + timedelta(hours=settings.auth_session_absolute_hours),
    )
    return raw_token, record


def load_authenticated_session(
    session: Session,
    request: Request,
    settings: Settings,
) -> AuthenticatedSession | None:
    """Resolve, expire, and optionally touch one server-side session."""

    raw_token = request.cookies.get(settings.auth_session_cookie_name)
    if not raw_token:
        return None
    try:
        record = find_session_by_hash(session, hash_session_token(raw_token))
    except ValueError:
        return None
    if record is None or not is_session_active(record):
        return None
    account = session.get(UserAccount, record.user_id)
    if account is None or account.status != UserAccountStatus.ACTIVE.value:
        if record.revoked_at is None:
            revoke_session(session, session_id=record.id, reason="account unavailable")
        return None
    touch_session(
        session,
        record,
        now=datetime.now(UTC),
        idle_timeout=timedelta(minutes=settings.auth_session_idle_minutes),
        update_interval=timedelta(
            seconds=settings.auth_session_activity_update_seconds
        ),
    )
    return AuthenticatedSession(raw_token=raw_token, record=record, account=account)
