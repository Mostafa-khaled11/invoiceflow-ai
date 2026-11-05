"""Unit tests for the private invoice storage service."""

import io
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from app.services.invoice_storage import InvoiceStorage, InvoiceStorageError

PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<</Type/Catalog>>\nendobj\n%%EOF\n"


class _FailingReadPayload(io.BytesIO):
    """BytesIO whose reads fail right after producing the first chunk.

    The failing read simulates a dead stream midway through the staging
    copy so the service's own partial-file cleanup can be asserted.
    """

    def __init__(self, seed: bytes) -> None:
        super().__init__(seed)
        self._reads = 0

    def read(self, size: Any = None, /) -> bytes:
        self._reads += 1
        if self._reads > 1:
            raise OSError("simulated read failure")
        return super().read(size)


def _make_storage(tmp_path: Path) -> InvoiceStorage:
    storage = InvoiceStorage(tmp_path / "root")
    return storage


def test_stage_and_commit_roundtrip_preserves_bytes(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    key = f"{uuid4()}.pdf"

    staged = storage.stage_upload(io.BytesIO(PDF_BYTES))
    final = storage.commit_staged(staged, key)

    assert final == storage.root / key
    assert final.is_file()
    assert final.read_bytes() == PDF_BYTES
    assert not staged.exists()
    assert list((storage.root / ".staging").iterdir()) == []


def test_staged_filenames_never_come_from_outside_the_service(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)

    staged = storage.stage_upload(io.BytesIO(PDF_BYTES))

    assert staged.parent == storage.root / ".staging"
    assert staged.name.endswith(".part")
    assert "invoice" not in staged.name and ".." not in staged.name


def test_commit_refuses_to_overwrite_existing_file(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    key = f"{uuid4()}.pdf"

    existing = storage.root / key
    existing.write_bytes(b"previous content")

    staged = storage.stage_upload(io.BytesIO(PDF_BYTES))
    try:
        storage.commit_staged(staged, key)
        raise AssertionError("commit_staged should have raised")
    except InvoiceStorageError:
        # Staged bytes are retained so the caller can decide their fate.
        assert staged.exists()
        assert existing.read_bytes() == b"previous content"


def test_abandon_removes_staged_and_moved_files(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    key = f"{uuid4()}.pdf"

    staged = storage.stage_upload(io.BytesIO(PDF_BYTES))
    storage.commit_staged(staged, key)
    storage.abandon(storage.root / "missing.part", key)

    assert not (storage.root / key).exists()


def test_discard_removes_stage(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)

    staged = storage.stage_upload(io.BytesIO(PDF_BYTES))
    storage.discard(staged)

    assert list((storage.root / ".staging").iterdir()) == []


@pytest.mark.parametrize(
    "unsafe_key",
    (
        "../invoice.pdf",
        r"..\invoice.pdf",
        "sub/invoice.pdf",
        r"sub\invoice.pdf",
        "/invoice.pdf",
        r"C:\temp\invoice.pdf",
        "C:invoice.pdf",
        ".",
        "..",
        "",
    ),
)
def test_unsafe_storage_keys_are_rejected(
    tmp_path: Path, unsafe_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = _make_storage(tmp_path)

    def unexpected_replace(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("unsafe key reached filesystem replacement")

    monkeypatch.setattr("app.services.invoice_storage.os.replace", unexpected_replace)
    with pytest.raises(InvoiceStorageError, match="unsafe storage key"):
        storage.commit_staged(storage.root / "whatever.pdf", unsafe_key)


def test_staging_failure_cleans_the_partial_file(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)

    payload = _FailingReadPayload(PDF_BYTES)
    try:
        storage.stage_upload(payload)
        raise AssertionError("stage_upload should have raised")
    except InvoiceStorageError:
        assert list((storage.root / ".staging").iterdir()) == []
