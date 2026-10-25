"""Classic invoice template."""

from pathlib import Path

from evaluation.models import InvoiceSpec
from evaluation.pdf_renderer import render_invoice_pdf


def render(spec: InvoiceSpec, output_path: Path) -> None:
    render_invoice_pdf(spec, output_path, style="classic")
