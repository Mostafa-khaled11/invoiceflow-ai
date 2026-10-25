"""Small provider boundary for structured invoice extraction."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from time import sleep
from typing import Any, Protocol

from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    Groq,
    InternalServerError,
    RateLimitError,
)

from app.core.config import Settings
from app.prompts.invoice_extraction_v1 import build_invoice_messages


@dataclass(frozen=True, slots=True)
class InvoiceLLMResponse:
    """Provider output normalized for the domain service."""

    data: dict[str, Any]
    raw_model_output: dict[str, Any]
    input_tokens: int | None
    output_tokens: int | None
    attempt_count: int


class InvoiceLLMProviderError(RuntimeError):
    """Safe provider failure without secrets, prompts, or invoice text."""

    def __init__(
        self,
        error_code: str,
        *,
        attempt_count: int,
        retryable: bool,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.attempt_count = attempt_count
        self.retryable = retryable


class InvoiceLLMProvider(Protocol):
    """Provider abstraction consumed by structured extraction orchestration."""

    provider_name: str
    model_name: str

    def extract(
        self,
        document_text: str,
        *,
        schema: dict[str, Any],
    ) -> InvoiceLLMResponse:
        """Return strict structured output for one document."""

        ...


class GroqInvoiceLLMProvider:
    """Groq implementation using non-streaming strict JSON Schema output."""

    provider_name = "groq"

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self.model_name = settings.groq_model
        self._max_retries = settings.llm_max_retries
        self._sleep = sleep_fn
        self._client = client
        if self._client is None and settings.groq_api_key:
            self._client = Groq(
                api_key=settings.groq_api_key,
                timeout=settings.llm_timeout_seconds,
                max_retries=0,
            )

    def extract(
        self,
        document_text: str,
        *,
        schema: dict[str, Any],
    ) -> InvoiceLLMResponse:
        if self._client is None:
            raise InvoiceLLMProviderError(
                "llm_not_configured", attempt_count=0, retryable=False
            )
        messages = build_invoice_messages(document_text)
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "invoice_extraction_v1",
                "strict": True,
                "schema": schema,
            },
        }
        for attempt in range(1, self._max_retries + 2):
            try:
                client: Any = self._client
                response = client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    temperature=0,
                    stream=False,
                    response_format=response_format,
                )
                content = response.choices[0].message.content
                if not isinstance(content, str):
                    raise InvoiceLLMProviderError(
                        "invalid_provider_output",
                        attempt_count=attempt,
                        retryable=False,
                    )
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as exc:
                    raise InvoiceLLMProviderError(
                        "invalid_provider_output",
                        attempt_count=attempt,
                        retryable=False,
                    ) from exc
                if not isinstance(parsed, dict):
                    raise InvoiceLLMProviderError(
                        "invalid_provider_output",
                        attempt_count=attempt,
                        retryable=False,
                    )
                usage = getattr(response, "usage", None)
                return InvoiceLLMResponse(
                    data=parsed,
                    raw_model_output=parsed,
                    input_tokens=_usage_value(usage, "prompt_tokens"),
                    output_tokens=_usage_value(usage, "completion_tokens"),
                    attempt_count=attempt,
                )
            except InvoiceLLMProviderError:
                raise
            except Exception as exc:
                retryable, error_code = _classify_provider_error(exc)
                if not retryable or attempt > self._max_retries:
                    raise InvoiceLLMProviderError(
                        error_code,
                        attempt_count=attempt,
                        retryable=retryable,
                    ) from exc
                self._sleep(2 ** (attempt - 1))

        raise AssertionError("provider retry loop must return or raise")


def _usage_value(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None) if usage is not None else None
    return value if isinstance(value, int) else None


def _classify_provider_error(error: Exception) -> tuple[bool, str]:
    if isinstance(error, RateLimitError):
        return True, "provider_rate_limited"
    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return True, "provider_unavailable"
    if isinstance(error, InternalServerError):
        return True, "provider_server_error"
    if isinstance(error, APIStatusError) and error.status_code >= 500:
        return True, "provider_server_error"
    return False, "provider_request_failed"
