"""Deterministic digital-text extraction from stored invoice PDFs."""

from dataclasses import dataclass
from time import perf_counter

from pypdf import PdfReader
from pypdf import __version__ as PYPDF_VERSION

from app.models.invoice_text_extraction import InvoiceTextExtractionStatus
from app.services.invoice_storage import InvoiceStorage, InvoiceStorageError

EXTRACTOR_NAME = "pypdf"
ERROR_STORED_FILE_MISSING = "stored_file_missing"
ERROR_PDF_PARSE = "pdf_parse_error"
ERROR_ENCRYPTED_PDF = "encrypted_pdf_unsupported"
ERROR_PAGE_LIMIT = "page_limit_exceeded"


@dataclass(frozen=True, slots=True)
class TextExtractionResult:
    """Typed internal result returned by the extraction service."""

    status: InvoiceTextExtractionStatus
    text_content: str
    page_count: int
    pages_with_text: int
    character_count: int
    non_whitespace_character_count: int
    processing_ms: int
    error_code: str | None = None
    extractor: str = EXTRACTOR_NAME
    extractor_version: str = PYPDF_VERSION


class InvoiceTextExtractor:
    """Extract the existing PDF text layer without semantic interpretation."""

    def extract(
        self,
        storage: InvoiceStorage,
        storage_key: str,
        *,
        max_pages: int,
        min_extracted_text_chars: int,
    ) -> TextExtractionResult:
        """Extract page text and classify the result using configured limits."""

        started = perf_counter()
        try:
            with storage.open_for_read(storage_key) as source:
                reader = PdfReader(source, strict=False)
                if reader.is_encrypted:
                    return self._failure(
                        started,
                        ERROR_ENCRYPTED_PDF,
                    )
                page_count = len(reader.pages)
                if page_count > max_pages:
                    return self._failure(
                        started,
                        ERROR_PAGE_LIMIT,
                        page_count=page_count,
                    )

                pages: list[str] = []
                pages_with_text = 0
                for page in reader.pages:
                    page_text = _normalize_page_text(page.extract_text() or "")
                    pages.append(page_text)
                    if page_text.strip():
                        pages_with_text += 1

        except InvoiceStorageError:
            return self._failure(started, ERROR_STORED_FILE_MISSING)
        except Exception:
            return self._failure(started, ERROR_PDF_PARSE)

        text_content = _normalize_document_text(pages)
        non_whitespace_count = len("".join(text_content.split()))
        status = (
            InvoiceTextExtractionStatus.TEXT_EXTRACTED
            if non_whitespace_count >= min_extracted_text_chars
            else InvoiceTextExtractionStatus.NEEDS_OCR
        )
        return TextExtractionResult(
            status=status,
            text_content=text_content,
            page_count=page_count,
            pages_with_text=pages_with_text,
            character_count=len(text_content),
            non_whitespace_character_count=non_whitespace_count,
            processing_ms=_elapsed_ms(started),
        )

    def _failure(
        self,
        started: float,
        error_code: str,
        *,
        page_count: int = 0,
    ) -> TextExtractionResult:
        return TextExtractionResult(
            status=InvoiceTextExtractionStatus.EXTRACTION_FAILED,
            text_content="",
            page_count=page_count,
            pages_with_text=0,
            character_count=0,
            non_whitespace_character_count=0,
            processing_ms=_elapsed_ms(started),
            error_code=error_code,
        )


def _normalize_page_text(value: str) -> str:
    """Apply only safe line-ending and parser-artifact normalization."""

    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def _normalize_document_text(pages: list[str]) -> str:
    """Join pages with a stable form-feed boundary and trim document edges."""

    return "\f".join(pages).strip(" \t\r\n")


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
