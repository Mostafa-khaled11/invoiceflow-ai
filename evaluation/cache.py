"""Inspectable, safe local prediction cache."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def cache_key(
    text: str, provider: str, model: str, prompt_version: str, schema_version: str
) -> str:
    identity = {
        "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PredictionCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def load(self, key: str, identity: dict[str, Any]) -> dict[str, Any] | None:
        path = self.directory / f"{key}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                value.get("cache_key") != key
                or value.get("identity") != identity
                or not isinstance(value.get("data"), dict)
            ):
                return None
            return value
        except (OSError, ValueError, TypeError):
            return None

    def save(
        self, key: str, identity: dict[str, Any], response: dict[str, Any]
    ) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {"cache_key": key, "identity": identity, **response}
        (self.directory / f"{key}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
