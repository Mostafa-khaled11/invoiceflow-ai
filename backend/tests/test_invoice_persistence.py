"""Persistence, storage, and duplicate-detection tests for invoice intake.

These tests exercise the Task 3.2 flow end to end. They run against the
PostgreSQL instance when `TEST_DATABASE_URL` is configured (see README) and
fall back to isolated SQLite files otherwise; the filesystem storage root is
always a per-test temporary directory.
"""

import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path

import app.services.invoice_intake as invoice_intake
import pytest
from app.models.invoice import Invoice, InvoiceStatus
from app.services.invoice_storage import InvoiceStorage, InvoiceStorageError
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

UPLOAD_URL = "/api/v1/invoices/upload"

SYNTHETIC_PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<</Type/Catalog>>\nendobj\n%%EOF\n"
SYNTHETIC_PDF_VARIANT = b"%PDF-1.7\n2 0 obj\n<</Type/Catalog>>\nendobj\n%%EOF\n"

RESPONSE_KEYS = {
    "invoice_id",
    "filename",
    "content_type",
    "size_bytes",
    "sha256",
    "status",
    "created_at",
}


def _valid_upload(name: str = "invoice.pdf") -> dict[str, tuple[str, bytes, str]]:
    """Return multipart data for a valid synthetic PDF upload."""

    return {"file": (name, SYNTHETIC_PDF_BYTES, "application/pdf")}


def _valid_variant_upload(
    name: str = "invoice.pdf",
) -> dict[str, tuple[str, bytes, str]]:
    """Return multipart data carrying different bytes under the same name."""

    return {"file": (name, SYNTHETIC_PDF_VARIANT, "application/pdf")}


def _invoice_count(engine: Engine) -> int:
    with Session(engine) as session:
        return int(
            session.execute(select(func.count()).select_from(Invoice)).scalar_one()
        )


def _permanent_storage_files(storage_root: Path) -> list[str]:
    return sorted(path.name for path in storage_root.iterdir() if path.is_file())


def _staging_is_empty(storage_root: Path) -> bool:
    staging = storage_root / ".staging"
    return not staging.is_dir() or list(staging.iterdir()) == []


@pytest.fixture
def seeded_same_sha_invoice(engine: Engine) -> Iterator[uuid.UUID]:
    """Insert a metadata-only invoice row claiming SYNTHETIC_PDF_BYTES' hash."""

    sha256_value = hashlib.sha256(SYNTHETIC_PDF_BYTES).hexdigest()
    invoice = Invoice(
        original_filename="seed.pdf",
        storage_key="00000000-0000-0000-0000-000000000000.pdf",
        content_type="application/pdf",
        size_bytes=len(SYNTHETIC_PDF_BYTES),
        sha256=sha256_value,
        status=InvoiceStatus.RECEIVED.value,
    )
    with Session(engine) as session:
        session.add(invoice)
        session.commit()
        invoice_id = invoice.id
    yield invoice_id


def test_valid_upload_persists_exactly_one_invoice(
    client: TestClient,
    engine: Engine,
    storage_root: Path,
) -> None:
    response = client.post(UPLOAD_URL, files=_valid_upload())

    assert response.status_code == 201
    payload = response.json()
    assert set(payload) == RESPONSE_KEYS
    assert payload["status"] == "received"
    assert payload["size_bytes"] == len(SYNTHETIC_PDF_BYTES)

    assert _invoice_count(engine) == 1
    assert _permanent_storage_files(storage_root) == [f"{payload['invoice_id']}.pdf"]


def test_stored_metadata_matches_the_upload(
    client: TestClient,
    engine: Engine,
) -> None:
    response = client.post(UPLOAD_URL, files=_valid_upload(name="report A.pdf"))

    assert response.status_code == 201
    expected_sha256 = hashlib.sha256(SYNTHETIC_PDF_BYTES).hexdigest()

    with Session(engine) as session:
        stored = session.execute(select(Invoice)).scalar_one()
        assert stored.original_filename == "report A.pdf"
        assert stored.content_type == "application/pdf"
        assert stored.size_bytes == len(SYNTHETIC_PDF_BYTES)
        assert stored.sha256 == expected_sha256
        assert stored.status == InvoiceStatus.RECEIVED.value
        assert stored.created_at is not None
        assert stored.updated_at is not None
        # The physical key is internally generated, never the client name.
        assert stored.storage_key == f"{stored.id}.pdf"


def test_stored_file_lives_privately_with_internal_key(
    client: TestClient,
    storage_root: Path,
) -> None:
    response = client.post(UPLOAD_URL, files=_valid_upload())
    invoice_id = response.json()["invoice_id"]

    permanent_files = _permanent_storage_files(storage_root)
    assert permanent_files == [f"{invoice_id}.pdf"]

    stored_path = storage_root / f"{invoice_id}.pdf"
    assert (
        hashlib.sha256(stored_path.read_bytes()).hexdigest()
        == response.json()["sha256"]
    )
    assert _staging_is_empty(storage_root)
    # The user-controlled display name never appears as a physical name.
    assert "invoice.pdf" not in permanent_files


def test_metadata_is_not_exposed_from_the_response_body(
    client: TestClient,
) -> None:
    response = client.post(UPLOAD_URL, files=_valid_upload())
    payload = response.json()

    assert set(payload) == RESPONSE_KEYS
    flat = str(payload).lower()
    assert "storage_key" not in flat


def test_get_endpoint_returns_stored_metadata(client: TestClient) -> None:
    created = client.post(UPLOAD_URL, files=_valid_upload()).json()
    invoice_id = created["invoice_id"]

    fetched = client.get(f"/api/v1/invoices/{invoice_id}")
    assert fetched.status_code == 200
    assert set(fetched.json()) == RESPONSE_KEYS
    assert fetched.json() == created

    assert client.get(f"/api/v1/invoices/{uuid.uuid4()}").status_code == 404
    assert client.get("/api/v1/invoices/not-a-uuid").status_code == 422


def test_identical_pdf_upload_is_rejected_as_conflict(
    client: TestClient,
    engine: Engine,
    storage_root: Path,
) -> None:
    first = client.post(UPLOAD_URL, files=_valid_upload())
    assert first.status_code == 201
    first_id = first.json()["invoice_id"]

    second = client.post(UPLOAD_URL, files=_valid_upload())
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["message"] == "Invoice already exists for the submitted content."
    assert detail["existing_invoice_id"] == first_id

    assert _invoice_count(engine) == 1
    assert len(_permanent_storage_files(storage_root)) == 1


def test_same_filename_with_different_content_is_accepted(
    client: TestClient,
    engine: Engine,
    storage_root: Path,
) -> None:
    first = client.post(UPLOAD_URL, files=_valid_upload())
    second = client.post(UPLOAD_URL, files=_valid_variant_upload())

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["filename"] == second.json()["filename"] == "invoice.pdf"
    assert first.json()["sha256"] != second.json()["sha256"]
    assert first.json()["invoice_id"] != second.json()["invoice_id"]

    assert _invoice_count(engine) == 2
    assert len(_permanent_storage_files(storage_root)) == 2


def test_failed_persistence_leaves_no_orphan_file(
    client: TestClient,
    engine: Engine,
    storage_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forced_failure(self: InvoiceStorage, staged: Path, key: str) -> Path:
        raise InvoiceStorageError("forced failure for test")

    monkeypatch.setattr(InvoiceStorage, "commit_staged", _forced_failure)

    response = client.post(UPLOAD_URL, files=_valid_upload())

    assert response.status_code == 500
    assert response.json() == {"detail": "The invoice could not be stored."}
    assert _invoice_count(engine) == 0
    assert _permanent_storage_files(storage_root) == []
    assert _staging_is_empty(storage_root)


def test_uniqueness_race_is_mapped_to_conflict(
    client: TestClient,
    seeded_same_sha_invoice: uuid.UUID,
    storage_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate a duplicate row appearing between pre-check and insert."""

    real_lookup = invoice_intake._find_existing_by_sha256
    calls = {"count": 0}

    def _skip_first_lookup(session: Session, sha256: str) -> Invoice | None:
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return real_lookup(session, sha256)

    monkeypatch.setattr(invoice_intake, "_find_existing_by_sha256", _skip_first_lookup)

    response = client.post(UPLOAD_URL, files=_valid_upload())

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["existing_invoice_id"] == str(seeded_same_sha_invoice)
    assert _staging_is_empty(storage_root)


def test_sha256_uniqueness_is_enforced_at_the_database_level(
    engine: Engine,
) -> None:
    sha256_value = hashlib.sha256(SYNTHETIC_PDF_BYTES).hexdigest()

    def _add_row(seed: str) -> None:
        with Session(engine) as session:
            session.add(
                Invoice(
                    original_filename="dup.pdf",
                    storage_key=f"{seed}.pdf",
                    content_type="application/pdf",
                    size_bytes=1,
                    sha256=sha256_value,
                    status=InvoiceStatus.RECEIVED.value,
                )
            )
            session.commit()

    _add_row("00000000-0000-0000-0000-000000000001")
    with pytest.raises(IntegrityError):
        _add_row("00000000-0000-0000-0000-000000000002")


def test_sha256_unique_index_exists_in_database(
    engine: Engine,
) -> None:
    """The UNIQUE sha256 protection must exist in the database schema."""

    inspector = inspect(engine)
    constraint_columns = [
        constraint["column_names"]
        for constraint in inspector.get_unique_constraints("invoices")
    ]
    assert any("sha256" in columns for columns in constraint_columns)
