"""Provider-neutral OIDC Authorization Code + PKCE boundary."""

from dataclasses import dataclass
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from app.core.config import Settings

_SUPPORTED_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
)


class OIDCError(RuntimeError):
    """Base class for safe, user-facing OIDC failures."""


class OIDCConfigurationError(OIDCError):
    """Raised when configured or discovered provider metadata is unsafe."""


class OIDCValidationError(OIDCError):
    """Raised when a provider response fails protocol validation."""


@dataclass(frozen=True)
class OIDCDiscovery:
    """Validated provider metadata used by the authorization flow."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True)
class VerifiedOIDCIdentity:
    """Minimal verified identity claims; tokens are never returned or persisted."""

    issuer: str
    subject: str
    email: str | None
    display_name: str | None
    email_verified: bool


class OIDCClient:
    """Small synchronous OIDC client with injectable HTTP transport for tests."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.Client(
            timeout=settings.oidc_timeout_seconds,
            follow_redirects=False,
        )

    def close(self) -> None:
        """Close the client created for a request, leaving injected clients open."""

        if self._owns_http_client:
            self._http_client.close()

    def _configured_issuer(self) -> str:
        issuer = (self.settings.oidc_issuer or "").strip().rstrip("/")
        if not issuer:
            raise OIDCConfigurationError("OIDC_ISSUER is not configured")
        parsed = urlparse(issuer)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise OIDCConfigurationError("OIDC_ISSUER must be an absolute HTTP URL")
        if self.settings.environment.lower() in {"production", "prod"}:
            if parsed.scheme != "https":
                raise OIDCConfigurationError("Production OIDC issuer must use HTTPS")
        return issuer

    def _validate_endpoint(self, endpoint: str, issuer: str) -> str:
        parsed_endpoint = urlparse(endpoint)
        parsed_issuer = urlparse(issuer)
        if (
            parsed_endpoint.scheme not in {"http", "https"}
            or not parsed_endpoint.netloc
            or parsed_endpoint.username
            or parsed_endpoint.password
            or parsed_endpoint.query
            or parsed_endpoint.fragment
        ):
            raise OIDCConfigurationError("OIDC metadata contains an unsafe endpoint")
        if parsed_endpoint.netloc != parsed_issuer.netloc:
            raise OIDCConfigurationError(
                "OIDC metadata endpoint is outside the configured issuer origin"
            )
        if (
            self.settings.environment.lower() in {"production", "prod"}
            and parsed_endpoint.scheme != "https"
        ):
            raise OIDCConfigurationError("Production OIDC endpoints must use HTTPS")
        return endpoint

    def discover(self) -> OIDCDiscovery:
        """Fetch and validate pinned discovery metadata."""

        issuer = self._configured_issuer()
        discovery_url = f"{issuer}/.well-known/openid-configuration"
        try:
            response = self._http_client.get(discovery_url)
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("OIDC discovery failed") from exc
        if not isinstance(document, dict):
            raise OIDCValidationError("OIDC discovery response is not an object")
        discovered_issuer = document.get("issuer")
        if (
            not isinstance(discovered_issuer, str)
            or discovered_issuer.rstrip("/") != issuer
        ):
            raise OIDCValidationError(
                "OIDC discovery issuer does not match configuration"
            )
        try:
            authorization_endpoint = self._validate_endpoint(
                str(document["authorization_endpoint"]), issuer
            )
            token_endpoint = self._validate_endpoint(
                str(document["token_endpoint"]), issuer
            )
            jwks_uri = self._validate_endpoint(str(document["jwks_uri"]), issuer)
        except (KeyError, TypeError, OIDCConfigurationError) as exc:
            raise OIDCValidationError("OIDC discovery metadata is incomplete") from exc
        return OIDCDiscovery(
            issuer=issuer,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            jwks_uri=jwks_uri,
        )

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        """Build a fixed-callback Authorization Code + S256 PKCE request."""

        if not self.settings.oidc_client_id or not self.settings.oidc_redirect_uri:
            raise OIDCConfigurationError(
                "OIDC client and redirect settings are required"
            )
        discovery = self.discover()
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.oidc_client_id,
                "redirect_uri": self.settings.oidc_redirect_uri,
                "scope": self.settings.oidc_scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        parsed = urlparse(discovery.authorization_endpoint)
        return urlunparse(parsed._replace(query=query))

    def exchange_code(self, *, code: str, code_verifier: str) -> str:
        """Exchange one authorization code and return only its ID token."""

        if not self.settings.oidc_client_id or not self.settings.oidc_client_secret:
            raise OIDCConfigurationError("OIDC client credentials are required")
        if not self.settings.oidc_redirect_uri:
            raise OIDCConfigurationError("OIDC redirect URI is required")
        discovery = self.discover()
        try:
            response = self._http_client.post(
                discovery.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.settings.oidc_redirect_uri,
                    "client_id": self.settings.oidc_client_id,
                    "client_secret": self.settings.oidc_client_secret,
                    "code_verifier": code_verifier,
                },
            )
            response.raise_for_status()
            document = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("OIDC authorization code exchange failed") from exc
        id_token = document.get("id_token") if isinstance(document, dict) else None
        if not isinstance(id_token, str) or not id_token:
            raise OIDCValidationError("OIDC token response did not contain an ID token")
        return id_token

    def validate_id_token(self, *, id_token: str, nonce: str) -> VerifiedOIDCIdentity:
        """Verify signature, trusted key, issuer, audience, nonce, and time claims."""

        if not self.settings.oidc_client_id:
            raise OIDCConfigurationError("OIDC client ID is required")
        discovery = self.discover()
        try:
            response = self._http_client.get(discovery.jwks_uri)
            response.raise_for_status()
            jwks_document = response.json()
            key_set = KeySet.import_key_set(jwks_document)
            token = jwt.decode(
                id_token,
                key_set,
                algorithms=_SUPPORTED_ALGORITHMS,
            )
            claims = token.claims
            registry = JWTClaimsRegistry(
                iss={"essential": True, "value": discovery.issuer},
                aud={"essential": True, "value": self.settings.oidc_client_id},
                sub={"essential": True},
                exp={"essential": True},
                iat={"essential": True},
                nbf={"essential": False},
                nonce={"essential": True, "value": nonce},
                leeway=5,
            )
            registry.validate(claims)
        except Exception as exc:
            if isinstance(exc, OIDCError):
                raise
            raise OIDCValidationError("OIDC ID token validation failed") from exc

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OIDCValidationError("OIDC subject is missing")
        email = claims.get("email")
        display_name = claims.get("name") or claims.get("preferred_username")
        return VerifiedOIDCIdentity(
            issuer=discovery.issuer,
            subject=subject,
            email=email if isinstance(email, str) else None,
            display_name=display_name if isinstance(display_name, str) else None,
            email_verified=claims.get("email_verified") is True,
        )
