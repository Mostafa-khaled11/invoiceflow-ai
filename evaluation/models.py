"""Typed models shared by the synthetic dataset generator and validator."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.schemas.structured_extractions import StructuredInvoiceData  # noqa: E402

Split = Literal["development", "holdout"]
Difficulty = Literal["easy", "medium", "hard"]
TemplateName = Literal[
    "template_a", "template_b", "template_c", "template_d", "template_e"
]
CurrencyCase = Literal["explicit", "ambiguous_symbol"]
LineItemGroup = Literal["1", "2-3", "4-5", "6-10"]


class DatasetMetadata(BaseModel):
    """Benchmark characteristics without expected prediction values."""

    model_config = ConfigDict(extra="forbid")

    invoice_id: str
    dataset_version: str
    schema_version: str
    split: Split
    difficulty: Difficulty
    template: TemplateName
    line_item_count: int = Field(ge=1, le=10)
    line_item_group: LineItemGroup
    has_vendor_tax_id: bool
    has_due_date: bool
    has_po_number: bool
    has_discount: bool
    has_shipping: bool
    currency_case: CurrencyCase
    date_format: str
    adversarial: bool
    multi_page: bool
    tax_presentation: Literal["line_items", "summary"]


class DatasetManifest(BaseModel):
    """Deterministic dataset-level summary."""

    model_config = ConfigDict(extra="forbid")

    dataset_version: str
    schema_version: str
    generator_version: str
    seed: int
    total_documents: int
    development_documents: int
    holdout_documents: int
    difficulty_distribution: dict[str, int]
    split_difficulty_distribution: dict[str, dict[str, int]]
    templates: dict[str, int]
    missing_field_distribution: dict[str, int]
    currency_cases: dict[str, int]
    line_item_groups: dict[str, int]
    adversarial_documents: int
    multi_page_documents: int


@dataclass(frozen=True, slots=True)
class InvoiceSpec:
    """Complete generated source/ground-truth document before rendering."""

    invoice_id: str
    split: Split
    difficulty: Difficulty
    template: TemplateName
    metadata: DatasetMetadata
    ground_truth: StructuredInvoiceData
    source_text: str
    render_data: dict[str, Any]
