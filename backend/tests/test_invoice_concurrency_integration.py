"""Real-PostgreSQL tests for invoice locking and extraction persistence."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.models.invoice import Invoice, InvoiceStatus
from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
from app.models.invoice_text_extraction import InvoiceTextExtraction
from app.services.invoice_llm import InvoiceLLMResponse
from app.services.invoice_lock import lock_invoice_for_update
from app.services.structured_invoice_extraction import (
    StructuredExtractionServiceError,
    StructuredInvoiceExtractionService,
)
from sqlalchemy import Engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.test_structured_invoice_extraction import _valid_data

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_engine(engine: Engine) -> Engine:
    """Run these tests only when the configured fixture is real PostgreSQL."""

    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL is not configured")
    return engine


def _seed_invoice(engine: Engine) -> UUID:
    with Session(engine) as session:
        invoice = Invoice(
            original_filename="concurrency.pdf",
            storage_key=f"{uuid4()}.pdf",
            content_type="application/pdf",
            size_bytes=64,
            sha256=uuid4().hex + uuid4().hex,
            status=InvoiceStatus.RECEIVED.value,
        )
        session.add(invoice)
        session.commit()
        return invoice.id


def _seed_text_invoice(engine: Engine) -> UUID:
    with Session(engine) as session:
        invoice_id = uuid4()
        text_id = uuid4()
        session.add(
            Invoice(
                id=invoice_id,
                original_filename="concurrency.pdf",
                storage_key=f"{uuid4()}.pdf",
                content_type="application/pdf",
                size_bytes=64,
                sha256=uuid4().hex + uuid4().hex,
                status=InvoiceStatus.TEXT_EXTRACTED.value,
            )
        )
        session.add(
            InvoiceTextExtraction(
                id=text_id,
                invoice_id=invoice_id,
                extractor="test",
                extractor_version="test",
                text_content="Synthetic invoice text for concurrency testing.",
                page_count=1,
                pages_with_text=1,
                character_count=49,
                non_whitespace_character_count=42,
                status="text_extracted",
                processing_ms=1,
            )
        )
        session.commit()
        return invoice_id


class _Provider:
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(
        self,
        *,
        barrier: Barrier | None = None,
        started: Event | None = None,
        release: Event | None = None,
    ) -> None:
        self.barrier = barrier
        self.started = started
        self.release = release

    def extract(
        self, document_text: str, *, schema: dict[str, object]
    ) -> InvoiceLLMResponse:
        if self.started is not None:
            self.started.set()
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        if self.release is not None:
            if not self.release.wait(timeout=5):
                raise TimeoutError("provider release was not signaled")
        data = _valid_data()
        return InvoiceLLMResponse(
            data=data,
            raw_model_output=data,
            input_tokens=1,
            output_tokens=1,
            attempt_count=1,
        )


def _settings(engine: Engine) -> Settings:
    return Settings(database_url=str(engine.url))


def test_invoice_lock_blocks_conflicting_transaction(postgres_engine: Engine) -> None:
    invoice_id = _seed_invoice(postgres_engine)
    first = Session(postgres_engine)
    assert lock_invoice_for_update(first, invoice_id) is not None

    attempted = Event()
    entered = Event()
    errors: list[BaseException] = []

    def second_transaction() -> None:
        try:
            attempted.set()
            with Session(postgres_engine) as second:
                assert lock_invoice_for_update(second, invoice_id) is not None
                entered.set()
                second.rollback()
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(second_transaction)
        assert attempted.wait(timeout=2)
        assert not entered.wait(timeout=0.25)
        first.rollback()
        assert entered.wait(timeout=2)
        future.result(timeout=2)
    first.close()
    assert not errors


def test_different_invoice_locks_do_not_block_each_other(
    postgres_engine: Engine,
) -> None:
    invoice_a = _seed_invoice(postgres_engine)
    invoice_b = _seed_invoice(postgres_engine)
    first = Session(postgres_engine)
    assert lock_invoice_for_update(first, invoice_a) is not None

    entered = Event()

    def second_transaction() -> None:
        with Session(postgres_engine) as second:
            assert lock_invoice_for_update(second, invoice_b) is not None
            entered.set()
            second.rollback()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(second_transaction)
        assert entered.wait(timeout=2)
        future.result(timeout=2)
    first.rollback()
    first.close()


def test_concurrent_identical_extractions_persist_one_success(
    postgres_engine: Engine,
) -> None:
    invoice_id = _seed_text_invoice(postgres_engine)
    barrier = Barrier(2)

    def run_extraction() -> UUID:
        provider = _Provider(barrier=barrier)
        with Session(postgres_engine) as session:
            response = StructuredInvoiceExtractionService(
                session, provider, _settings(postgres_engine)
            ).extract(invoice_id)
            return response.structured_extraction_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_extraction) for _ in range(2)]
        result_ids = [future.result(timeout=10) for future in futures]

    assert result_ids[0] == result_ids[1]
    with Session(postgres_engine) as session:
        count = session.execute(
            select(func.count()).select_from(InvoiceStructuredExtraction)
        ).scalar_one()
    assert count == 1


def test_extraction_persistence_failure_rolls_back_atomically(
    postgres_engine: Engine,
) -> None:
    invoice_id = _seed_text_invoice(postgres_engine)
    provider = _Provider()

    with Session(postgres_engine) as session:

        def fail_after_flush(_session: Session, _flush_context: Any) -> None:
            raise SQLAlchemyError("controlled post-flush failure")

        event.listen(session, "after_flush", fail_after_flush)
        try:
            with pytest.raises(StructuredExtractionServiceError):
                StructuredInvoiceExtractionService(
                    session, provider, _settings(postgres_engine)
                ).extract(invoice_id)
        finally:
            event.remove(session, "after_flush", fail_after_flush)

    with Session(postgres_engine) as session:
        invoice = session.get(Invoice, invoice_id)
        extraction_count = session.execute(
            select(func.count()).select_from(InvoiceStructuredExtraction)
        ).scalar_one()
    assert invoice is not None
    assert invoice.status == InvoiceStatus.TEXT_EXTRACTED.value
    assert extraction_count == 0


def test_provider_wait_does_not_hold_invoice_lock(
    postgres_engine: Engine,
) -> None:
    invoice_id = _seed_text_invoice(postgres_engine)
    provider_started = Event()
    release_provider = Event()
    provider = _Provider(started=provider_started, release=release_provider)

    def run_extraction() -> UUID:
        with Session(postgres_engine) as session:
            response = StructuredInvoiceExtractionService(
                session, provider, _settings(postgres_engine)
            ).extract(invoice_id)
            return response.structured_extraction_id

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_extraction)
        assert provider_started.wait(timeout=2)
        with Session(postgres_engine) as second:
            assert lock_invoice_for_update(second, invoice_id) is not None
            second.rollback()
        release_provider.set()
        future.result(timeout=5)
