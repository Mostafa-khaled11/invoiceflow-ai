"""Tests for the Task 3.3 digital PDF text extraction baseline."""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID

from app.core.config import Settings
from app.models.invoice_text_extraction import InvoiceTextExtraction
from app.services.invoice_text_extraction import InvoiceTextExtractor
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

UPLOAD_URL = "/api/v1/invoices/upload"


def _pdf_with_text(pages: list[str]) -> bytes:
    """Build a small synthetic text PDF without adding a PDF fixture file."""

    font_id = 3
    page_ids = [4 + index * 2 for index in range(len(pages))]
    content_ids = [page_id + 1 for page_id in page_ids]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            b"<< /Type /Pages /Kids ["
            + b" ".join(f"{page_id} 0 R".encode() for page_id in page_ids)
            + f"] /Count {len(pages)} >>".encode()
        ),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, content_id, text in zip(page_ids, content_ids, pages, strict=True):
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
        objects[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 3 0 R >> >> /Contents "
            f"{content_id} 0 R >>"
        ).encode()
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * (max(objects) + 1)
    for object_id in sorted(objects):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode())
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def _encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("synthetic-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _upload(client: TestClient, pdf: bytes) -> UUID:
    response = client.post(
        UPLOAD_URL,
        files={"file": ("synthetic.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 201
    return UUID(response.json()["invoice_id"])


def test_digital_text_is_persisted_and_classified(
    client: TestClient,
    engine: Engine,
    storage_root: Path,
) -> None:
    invoice_id = _upload(
        client,
        _pdf_with_text(
            [
                "Vendor invoice number 12345 total 100.00 USD issued for "
                "synthetic validation."
            ]
        ),
    )

    response = client.post(f"/api/v1/invoices/{invoice_id}/extract-text")

    assert response.status_code == 200
    payload = response.json()
    assert payload["invoice_id"] == str(invoice_id)
    assert payload["status"] == "text_extracted"
    assert payload["extractor"] == "pypdf"
    assert payload["page_count"] == 1
    assert payload["pages_with_text"] == 1
    assert payload["non_whitespace_character_count"] >= 50
    assert "text_content" not in payload
    assert str(storage_root) not in str(payload)

    with Session(engine) as session:
        stored = session.get(InvoiceTextExtraction, UUID(payload["extraction_id"]))
        assert stored is not None
        assert stored.text_content == (
            "Vendor invoice number 12345 total 100.00 USD issued for "
            "synthetic validation."
        )
        assert stored.processing_ms >= 0
        assert (
            session.execute(
                select(func.count()).select_from(InvoiceTextExtraction)
            ).scalar_one()
            == 1
        )


def test_multi_page_text_preserves_form_feed_boundaries(
    client: TestClient,
    engine: Engine,
) -> None:
    invoice_id = _upload(
        client,
        _pdf_with_text(
            [
                "First page has enough synthetic text for extraction.",
                "Second page also has enough synthetic text for extraction.",
            ]
        ),
    )

    response = client.post(f"/api/v1/invoices/{invoice_id}/extract-text")

    assert response.status_code == 200
    assert response.json()["page_count"] == 2
    assert response.json()["pages_with_text"] == 2
    with Session(engine) as session:
        stored = session.get(
            InvoiceTextExtraction, UUID(response.json()["extraction_id"])
        )
        assert stored is not None
        assert "\f" in stored.text_content


def test_empty_text_pdf_is_routed_to_needs_ocr(client: TestClient) -> None:
    invoice_id = _upload(client, _pdf_with_text([""]))

    response = client.post(f"/api/v1/invoices/{invoice_id}/extract-text")

    assert response.status_code == 200
    assert response.json()["status"] == "needs_ocr"
    assert response.json()["error_code"] is None
    assert response.json()["page_count"] == 1
    assert response.json()["pages_with_text"] == 0
    assert response.json()["non_whitespace_character_count"] == 0


def test_encrypted_pdf_is_a_controlled_failure(client: TestClient) -> None:
    invoice_id = _upload(client, _encrypted_pdf())

    response = client.post(f"/api/v1/invoices/{invoice_id}/extract-text")

    assert response.status_code == 200
    assert response.json()["status"] == "extraction_failed"
    assert response.json()["error_code"] == "encrypted_pdf_unsupported"
    assert "traceback" not in response.text.lower()


def test_page_limit_is_enforced(
    client: TestClient,
    app_settings: Settings,
) -> None:
    invoice_id = _upload(client, _pdf_with_text(["page text"] * 3))
    app_settings.max_invoice_pages = 2

    response = client.post(f"/api/v1/invoices/{invoice_id}/extract-text")

    assert response.status_code == 200
    assert response.json()["status"] == "extraction_failed"
    assert response.json()["error_code"] == "page_limit_exceeded"
    assert response.json()["page_count"] == 3


def test_missing_stored_file_is_persisted_as_controlled_failure(
    client: TestClient,
    engine: Engine,
    storage_root: Path,
) -> None:
    invoice_id = _upload(client, _pdf_with_text(["Enough text for the baseline."]))
    (storage_root / f"{invoice_id}.pdf").unlink()

    response = client.post(f"/api/v1/invoices/{invoice_id}/extract-text")

    assert response.status_code == 200
    assert response.json()["status"] == "extraction_failed"
    assert response.json()["error_code"] == "stored_file_missing"
    with Session(engine) as session:
        stored = session.execute(
            select(InvoiceTextExtraction).where(
                InvoiceTextExtraction.invoice_id == invoice_id
            )
        ).scalar_one()
        assert stored.error_code == "stored_file_missing"


def test_invalid_invoice_id_returns_404(client: TestClient) -> None:
    response = client.post(f"/api/v1/invoices/{UUID(int=0)}/extract-text")

    assert response.status_code == 404


def test_existing_extraction_is_idempotent(
    client: TestClient,
    engine: Engine,
    monkeypatch,
) -> None:
    invoice_id = _upload(
        client,
        _pdf_with_text(["Enough synthetic text for idempotent extraction."]),
    )
    first = client.post(f"/api/v1/invoices/{invoice_id}/extract-text")
    assert first.status_code == 200

    def fail_if_called(*args, **kwargs):
        raise AssertionError("pypdf extraction should not rerun")

    monkeypatch.setattr(InvoiceTextExtractor, "extract", fail_if_called)
    second = client.post(f"/api/v1/invoices/{invoice_id}/extract-text")

    assert second.status_code == 200
    assert second.json()["extraction_id"] == first.json()["extraction_id"]
    with Session(engine) as session:
        assert (
            session.execute(
                select(func.count()).select_from(InvoiceTextExtraction)
            ).scalar_one()
            == 1
        )
