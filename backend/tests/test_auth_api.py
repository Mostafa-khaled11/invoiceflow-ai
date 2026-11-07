"""Offline OIDC protocol and browser-session regression tests."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from app.api.deps import get_oidc_client
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.models.identity import OIDCLoginTransaction, UserAccount, UserSession
from app.services.identity_persistence import (
    create_oidc_identity,
    create_session,
    create_user_account,
    hash_session_token,
)
from app.services.oidc import OIDCClient, OIDCValidationError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey, import_key
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ISSUER = "https://issuer.example.test"
CLIENT_ID = "invoiceflow-test-client"
REDIRECT_URI = "http://testserver/api/v1/auth/callback"


class FakeOIDCProvider:
    """Deterministic discovery, token, and JWKS responses for offline tests."""

    def __init__(self, settings: Settings) -> None:
        private_key = generate_private_key(public_exponent=65537, key_size=2048)
        self.private_key = RSAKey.import_key(
            private_key,
            parameters={"kid": "test-key"},
        )
        public_key = RSAKey.import_key(
            private_key.public_key(),
            parameters={"kid": "test-key"},
        )
        self.jwks = {"keys": [public_key.as_dict()]}
        self.settings = settings
        self.id_token = self.make_token()
        self.http_client = httpx.Client(transport=httpx.MockTransport(self._handle))
        self.client = OIDCClient(settings, http_client=self.http_client)

    def make_token(self, **overrides: object) -> str:
        now = datetime.now(UTC)
        claims: dict[str, object] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "subject-001",
            "email": "reviewer@example.test",
            "name": "Review User",
            "email_verified": True,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "nonce": getattr(self, "nonce", "test-nonce"),
        }
        claims.update(overrides)
        return jwt.encode(
            {"alg": "RS256", "kid": "test-key"},
            claims,
            self.private_key,
            algorithms=["RS256"],
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if request.url.path == "/jwks":
            return httpx.Response(200, json=self.jwks)
        if request.url.path == "/token":
            return httpx.Response(200, json={"id_token": self.id_token})
        return httpx.Response(404)


@pytest.fixture
def auth_context(tmp_path: Path):
    database_path = tmp_path / "auth.db"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    settings = Settings(
        database_url=database_url,
        invoice_storage_path=tmp_path / "storage",
        oidc_enabled=True,
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret="test-client-secret",
        oidc_redirect_uri=REDIRECT_URI,
        oidc_transaction_cookie_secret=Fernet.generate_key().decode(),
        auth_csrf_secret="test-csrf-secret-material-32-bytes!!",
        auth_trusted_origins="http://testserver",
    )
    provider = FakeOIDCProvider(settings)
    app = create_app(settings)
    app.dependency_overrides[get_oidc_client] = lambda: provider.client
    with Session(engine) as session:
        account = create_user_account(
            session,
            email="old@example.test",
            status="ACTIVE",
        )
        create_oidc_identity(
            session,
            user_id=account.id,
            issuer=ISSUER,
            subject="subject-001",
        )
        session.commit()
    with TestClient(app) as client:
        yield client, provider, engine, settings
    provider.http_client.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _login(client: TestClient) -> tuple[str, str]:
    response = client.get("/api/v1/auth/login", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    query = parse_qs(urlparse(location).query)
    return query["state"][0], query["nonce"][0]


def test_login_callback_session_and_csrf_logout_round_trip(auth_context) -> None:
    client, provider, engine, _settings = auth_context
    state, nonce = _login(client)
    provider.nonce = nonce
    provider.id_token = provider.make_token(nonce=nonce)

    callback = client.get(
        f"/api/v1/auth/callback?code=one-time-code&state={state}",
        follow_redirects=False,
    )
    assert callback.status_code == 200
    assert callback.json()["account"]["email"] == "reviewer@example.test"
    assert "id_token" not in callback.text
    assert "test-client-secret" not in callback.text
    session_cookie = client.cookies.get("invoiceflow_session")
    assert session_cookie
    set_cookie = callback.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Path=/" in set_cookie

    with Session(engine) as session:
        stored = session.execute(select(UserSession)).scalar_one()
        assert session_cookie not in stored.token_hash
        transactions = session.execute(select(OIDCLoginTransaction)).scalars().all()
        assert len(transactions) == 1
        assert transactions[0].consumed_at is not None
        assert state not in transactions[0].state_hash
        assert nonce not in transactions[0].nonce_hash

    current = client.get("/api/v1/auth/session")
    assert current.status_code == 200
    csrf = current.json()["csrf_token"]
    assert csrf
    logout = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/session").status_code == 401


def test_callback_rejects_state_mismatch_without_consuming_transaction(
    auth_context,
) -> None:
    client, provider, _engine, _settings = auth_context
    state, nonce = _login(client)
    provider.nonce = nonce
    provider.id_token = provider.make_token(nonce=nonce)

    rejected = client.get(
        "/api/v1/auth/callback?code=code&state=wrong-state",
        follow_redirects=False,
    )
    assert rejected.status_code == 400
    accepted = client.get(
        f"/api/v1/auth/callback?code=code&state={state}",
        follow_redirects=False,
    )
    assert accepted.status_code == 200


def test_callback_replay_is_rejected(auth_context) -> None:
    client, provider, _engine, _settings = auth_context
    state, nonce = _login(client)
    provider.nonce = nonce
    provider.id_token = provider.make_token(nonce=nonce)
    callback_url = f"/api/v1/auth/callback?code=code&state={state}"
    assert client.get(callback_url, follow_redirects=False).status_code == 200
    assert client.get(callback_url, follow_redirects=False).status_code == 400


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"iss": "https://untrusted.example.test"},
        {"aud": "another-client"},
        {"exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())},
        {"nbf": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())},
        {"nonce": "different-nonce"},
    ],
)
def test_id_token_claim_validation_rejects_invalid_claims(
    auth_context, claim_overrides
) -> None:
    _client, provider, _engine, settings = auth_context
    token = provider.make_token(**claim_overrides)
    with pytest.raises(OIDCValidationError):
        provider.client.validate_id_token(id_token=token, nonce="test-nonce")


def test_id_token_rejects_unknown_signing_key(auth_context) -> None:
    _client, provider, _engine, settings = auth_context
    other_private = RSAKey.import_key(
        generate_private_key(public_exponent=65537, key_size=2048),
        parameters={"kid": "other-key"},
    )
    token = jwt.encode(
        {"alg": "RS256", "kid": "other-key"},
        {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "subject-001",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "nonce": "test-nonce",
        },
        other_private,
        algorithms=["RS256"],
    )
    with pytest.raises(OIDCValidationError):
        provider.client.validate_id_token(id_token=token, nonce="test-nonce")


def test_id_token_rejects_unsupported_hmac_algorithm(auth_context) -> None:
    _client, provider, _engine, _settings = auth_context
    hmac_key = import_key(b"test-hmac-key-material-32-bytes!!", "oct")
    token = jwt.encode(
        {"alg": "HS256", "kid": "hmac-key"},
        {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "subject-001",
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "nonce": "test-nonce",
        },
        hmac_key,
        algorithms=["HS256"],
    )
    with pytest.raises(OIDCValidationError):
        provider.client.validate_id_token(id_token=token, nonce="test-nonce")


def test_malformed_discovery_is_rejected_without_network_fallback(auth_context) -> None:
    _client, _provider, _engine, settings = auth_context

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "issuer": ["not-an-issuer"],
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(OIDCValidationError):
            OIDCClient(settings, http_client=client).discover()
    finally:
        client.close()


def test_unknown_identity_is_not_provisioned(auth_context) -> None:
    client, provider, _engine, _settings = auth_context
    state, nonce = _login(client)
    provider.nonce = nonce
    provider.id_token = provider.make_token(sub="not-approved", nonce=nonce)
    response = client.get(
        f"/api/v1/auth/callback?code=code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_pending_account_cannot_receive_session(auth_context) -> None:
    client, provider, engine, _settings = auth_context
    with Session(engine) as session:
        account = session.execute(select(UserAccount)).scalar_one()
        account.status = "PENDING"
        session.commit()
    state, nonce = _login(client)
    provider.nonce = nonce
    provider.id_token = provider.make_token(nonce=nonce)
    response = client.get(
        f"/api/v1/auth/callback?code=code&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_logout_requires_origin_and_csrf(auth_context) -> None:
    client, provider, _engine, _settings = auth_context
    state, nonce = _login(client)
    provider.nonce = nonce
    provider.id_token = provider.make_token(nonce=nonce)
    assert (
        client.get(
            f"/api/v1/auth/callback?code=code&state={state}",
            follow_redirects=False,
        ).status_code
        == 200
    )
    assert client.post("/api/v1/auth/logout").status_code == 403
    assert (
        client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "http://testserver", "X-CSRF-Token": "wrong"},
        ).status_code
        == 403
    )


def test_malformed_session_cookie_is_rejected(auth_context) -> None:
    client, _provider, _engine, _settings = auth_context
    client.cookies.set("invoiceflow_session", "not-a-valid-session")
    assert client.get("/api/v1/auth/session").status_code == 401


def test_expired_session_cookie_is_rejected(auth_context) -> None:
    client, _provider, engine, _settings = auth_context
    now = datetime.now(UTC)
    raw_token = "expired-session-token"
    with Session(engine) as session:
        account = session.execute(select(UserAccount)).scalar_one()
        create_session(
            session,
            user_id=account.id,
            token_hash=hash_session_token(raw_token),
            created_at=now - timedelta(hours=2),
            last_activity_at=now - timedelta(hours=2),
            idle_expires_at=now - timedelta(minutes=1),
            absolute_expires_at=now + timedelta(hours=1),
        )
        session.commit()
    client.cookies.set("invoiceflow_session", raw_token)
    assert client.get("/api/v1/auth/session").status_code == 401


def test_oidc_transaction_cookie_uses_pkce_s256(auth_context) -> None:
    client, _provider, _engine, _settings = auth_context
    response = client.get("/api/v1/auth/login", follow_redirects=False)
    query = parse_qs(urlparse(response.headers["location"]).query)
    verifier = client.cookies.get("invoiceflow_oidc_transaction")
    assert verifier
    challenge = query["code_challenge"][0]
    assert challenge != verifier
    assert response.headers["set-cookie"].count("HttpOnly") == 1
    assert (
        len(base64.urlsafe_b64decode(challenge + "=="))
        == hashlib.sha256(b"test").digest_size
    )


def test_production_auth_configuration_fails_closed() -> None:
    settings = Settings(
        environment="production",
        auth_protection_complete=True,
    )
    with pytest.raises(RuntimeError):
        create_app(settings)


def test_auth_routes_are_disabled_by_default(client: TestClient) -> None:
    assert client.get("/api/v1/auth/login").status_code == 503
