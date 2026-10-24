"""Tests for the invoice PDF upload intake endpoint (Task 3.1 contract)."""

import hashlib
import uuid

from app.services.invoice_files import MAX_UPLOAD_BYTES
from fastapi.testclient import TestClient

UPLOAD_URL = "/api/v1/invoices/upload"

SYNTHETIC_PDF_BYTES = (
    b"%PDF-1.7\n1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj\n%%EOF\n"
)
SYNTHETIC_PDF_VARIANT = (
    b"%PDF-1.7\n2 0 obj\n<</Type/Catalog/Kids 3 0 R>>\nendobj\n%%EOF\n"
)

RESPONSE_KEYS = {
    "invoice_id",
    "filename",
    "content_type",
    "size_bytes",
    "sha256",
    "status",
    "created_at",
}


def _valid_upload() -> dict[str, tuple[str, bytes, str]]:
    """Return multipart data for a valid synthetic PDF upload."""

    return {"file": ("invoice.pdf", SYNTHETIC_PDF_BYTES, "application/pdf")}


def test_valid_pdf_upload_returns_created_metadata(client: TestClient) -> None:
    response = client.post(UPLOAD_URL, files=_valid_upload())

    assert response.status_code == 201
    payload = response.json()
    assert set(payload) == RESPONSE_KEYS
    assert payload["filename"] == "invoice.pdf"
    assert payload["content_type"] == "application/pdf"
    assert payload["size_bytes"] == len(SYNTHETIC_PDF_BYTES)
    assert payload["sha256"] == hashlib.sha256(SYNTHETIC_PDF_BYTES).hexdigest()
    assert payload["status"] == "received"


def test_upload_id_is_a_valid_uuid(client: TestClient) -> None:
    response = client.post(UPLOAD_URL, files=_valid_upload())

    assert response.status_code == 201
    # Round-tripping verifies the UUID format; the server mints the
    # identifier itself and never echoes a client-supplied value.
    invoice_id = uuid.UUID(response.json()["invoice_id"])
    assert invoice_id.version == 4


def test_upload_ids_are_not_repeated(client: TestClient) -> None:
    first = client.post(UPLOAD_URL, files=_valid_upload()).json()["invoice_id"]
    second = client.post(
        UPLOAD_URL,
        files={"file": ("invoice.pdf", SYNTHETIC_PDF_VARIANT, "application/pdf")},
    ).json()["invoice_id"]

    assert first != second


def test_empty_file_is_rejected(client: TestClient) -> None:
    response = client.post(
        UPLOAD_URL,
        files={"file": ("invoice.pdf", b"", "application/pdf")},
    )

    assert response.status_code == 400
    assert "detail" in response.json()


def test_text_file_is_rejected(client: TestClient) -> None:
    response = client.post(
        UPLOAD_URL,
        files={"file": ("invoice.txt", b"plain text", "text/plain")},
    )

    assert response.status_code == 415


def test_fake_pdf_content_is_rejected(client: TestClient) -> None:
    response = client.post(
        UPLOAD_URL,
        files={"file": ("invoice.pdf", b"definitely not a pdf", "application/pdf")},
    )

    assert response.status_code == 400


def test_incorrect_mime_type_is_rejected(client: TestClient) -> None:
    response = client.post(
        UPLOAD_URL,
        files={"file": ("invoice.pdf", SYNTHETIC_PDF_BYTES, "text/plain")},
    )

    assert response.status_code == 415


def test_file_larger_than_limit_is_rejected(client: TestClient) -> None:
    oversized = b"%PDF-1.7\n" + b"%" * MAX_UPLOAD_BYTES
    assert len(oversized) > MAX_UPLOAD_BYTES

    response = client.post(
        UPLOAD_URL,
        files={"file": ("invoice.pdf", oversized, "application/pdf")},
    )

    assert response.status_code == 413


def test_file_of_exactly_the_limit_is_accepted(client: TestClient) -> None:
    exactly_max = b"%PDF-1.7\n" + b"%" * (MAX_UPLOAD_BYTES - 9)
    assert len(exactly_max) == MAX_UPLOAD_BYTES

    response = client.post(
        UPLOAD_URL,
        files={"file": ("invoice.pdf", exactly_max, "application/pdf")},
    )

    assert response.status_code == 201
    assert response.json()["size_bytes"] == MAX_UPLOAD_BYTES


def test_uppercase_pdf_extension_is_accepted(client: TestClient) -> None:
    response = client.post(
        UPLOAD_URL,
        files={"file": ("invoice.PDF", SYNTHETIC_PDF_BYTES, "application/pdf")},
    )

    assert response.status_code == 201
    assert response.json()["filename"] == "invoice.PDF"


def test_traversal_filename_is_reduced_to_a_safe_display_name(
    client: TestClient,
) -> None:
    variants = (
        ("..\\..\\invoice.pdf", SYNTHETIC_PDF_BYTES),
        ("sneaky/../invoice.pdf", SYNTHETIC_PDF_VARIANT),
    )
    for traversal_name, content in variants:
        response = client.post(
            UPLOAD_URL,
            files={"file": (traversal_name, content, "application/pdf")},
        )

        assert response.status_code == 201, traversal_name
        assert response.json()["filename"] == "invoice.pdf"


def test_missing_file_returns_validation_error(client: TestClient) -> None:
    response = client.post(UPLOAD_URL)

    assert response.status_code == 422
