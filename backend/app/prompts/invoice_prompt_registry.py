"""Versioned invoice prompt selection without changing existing prompt v1."""

from collections.abc import Callable
from typing import Any

from app.prompts import invoice_extraction_v1, invoice_extraction_v2

PromptBuilder = Callable[[str], list[dict[str, Any]]]

PROMPT_BUILDERS: dict[str, PromptBuilder] = {
    invoice_extraction_v1.PROMPT_VERSION: invoice_extraction_v1.build_invoice_messages,
    invoice_extraction_v2.PROMPT_VERSION: invoice_extraction_v2.build_invoice_messages,
}


def get_invoice_prompt_builder(prompt_version: str) -> PromptBuilder:
    """Return the exact versioned prompt builder or fail explicitly."""

    try:
        return PROMPT_BUILDERS[prompt_version]
    except KeyError as exc:
        supported = ", ".join(sorted(PROMPT_BUILDERS))
        raise ValueError(
            f"unsupported invoice prompt version {prompt_version!r}; "
            f"supported versions: {supported}"
        ) from exc


def build_invoice_messages_for_version(
    document_text: str, prompt_version: str
) -> list[dict[str, Any]]:
    """Build messages using an explicitly selected prompt version."""

    return get_invoice_prompt_builder(prompt_version)(document_text)
