"""Generate a one-time n8n service token and its API-side verifier."""

import hashlib
import secrets


def main() -> None:
    """Print values for separate API and n8n runtime configuration."""

    token = secrets.token_urlsafe(32)
    verifier = hashlib.sha256(token.encode("utf-8")).hexdigest()
    print(f"INVOICEFLOW_N8N_SERVICE_TOKEN={token}")
    print(f"N8N_SERVICE_TOKEN_HASH={verifier}")


if __name__ == "__main__":
    main()
