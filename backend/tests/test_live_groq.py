"""Opt-in synthetic Groq contract smoke test."""

import os

import pytest
from app.core.config import Settings
from app.schemas.structured_extractions import StructuredInvoiceData
from app.services.invoice_llm import GroqInvoiceLLMProvider


@pytest.mark.integration
def test_optional_live_groq_contract() -> None:
    """Make at most one request and never run without explicit opt-in."""

    if os.getenv("RUN_LIVE_LLM_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_LLM_TESTS=1 to enable the live Groq smoke test")
    settings = Settings()
    if not settings.groq_api_key:
        pytest.skip("GROQ_API_KEY is required for the live Groq smoke test")

    provider = GroqInvoiceLLMProvider(settings)
    response = provider.extract(
        "Synthetic invoice. Vendor: Example Services. Invoice number: SYN-001. "
        "Invoice date: 2026-10-20. Currency: USD. Total amount: 125.00.",
        schema=StructuredInvoiceData.model_json_schema(),
    )
    StructuredInvoiceData.model_validate(response.data)
