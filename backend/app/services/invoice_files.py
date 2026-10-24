"""Intake validation for invoice PDF uploads.

Filenames, reported MIME types, and upload content are all untrusted client
input. Validation inspects the byte stream itself; nothing in this module
persists upload bytes to disk, exposes filesystem paths, or logs content.
"""

from dataclasses import dataclass
from hashlib import sha256
from typing import Final

from fastapi import UploadFile

MAX_UPLOAD_BYTES: Final[int] = 10 * 1024 * 1024
"""Hard product limit for a single invoice PDF (10 MB)."""

_HASH_CHUNK_BYTES: Final[int] = 1024 * 1024
"""Streaming chunk size used while hashing uploads."""

_PDF_HEADER_WINDOW: Final[int] = 1024
"""Number of leading bytes inspected for the PDF signature."""

_PDF_SIGNATURE: Final[bytes] = b"%PDF-"

_SUPPORTED_CONTENT_TYPE: Final[str] = "application/pdf"

_SUPPORTED_EXTENSION: Final[str] = ".pdf"


class UploadValidationError(Exception):
    """Base class for uploads rejected by intake validation.

    Concrete subclasses carry the HTTP status code and public detail message
    that the API layer translates into a predictable error response.
    """

    status_code: int = 400
    detail: str = "The uploaded invoice was rejected."


class MissingFilenameError(UploadValidationError):
    """The upload lacks a usable display filename."""

    status_code = 400
    detail = "The uploaded file has no usable filename."


class EmptyUploadError(UploadValidationError):
    """The upload contains no bytes."""

    status_code = 400
    detail = "The uploaded file is empty."


class UnsupportedExtensionError(UploadValidationError):
    """The filename does not carry the supported PDF extension."""

    status_code = 415
    detail = "Only .pdf files are supported."


class UnsupportedContentTypeError(UploadValidationError):
    """The reported content type is not appropriate for a PDF."""

    status_code = 415
    detail = "Only application/pdf uploads are supported."


class InvalidPdfSignatureError(UploadValidationError):
    """The content does not start with the expected PDF signature."""

    status_code = 400
    detail = "The uploaded file is not a valid PDF."


class OversizedUploadError(UploadValidationError):
    """The upload exceeds the supported maximum size."""

    status_code = 413
    detail = "The uploaded file exceeds the 10 MB limit."


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    """Intake metadata derived from a rejected-or-accepted upload stream."""

    filename: str
    content_type: str
    size_bytes: int
    sha256: str


def sanitize_display_filename(raw_filename: str | None) -> str:
    """Reduce an untrusted multipart filename to a safe display leaf name.

    Both Windows and POSIX path separators are treated as directory parts so
    no traversal sequence can survive. The result is display metadata only
    and is never used as a filesystem path.
    """

    if not raw_filename:
        return ""
    leaf = raw_filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if leaf in {".", ".."}:
        return ""
    return leaf


async def validate_invoice_pdf(upload: UploadFile) -> ValidatedUpload:
    """Validate an uploaded PDF and derive intake metadata from the stream.

    The file is read in bounded chunks so oversized uploads are rejected by
    size without being copied into memory in full, and hashing aborts as
    soon as the limit is exceeded.
    """

    display_name = sanitize_display_filename(upload.filename)
    if not display_name:
        raise MissingFilenameError()

    _stem, separator, extension = display_name.rpartition(".")
    if not separator or extension.lower() != "pdf":
        raise UnsupportedExtensionError()

    reported_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if reported_type != _SUPPORTED_CONTENT_TYPE:
        raise UnsupportedContentTypeError()

    head = await upload.read(_PDF_HEADER_WINDOW)
    if not head:
        raise EmptyUploadError()
    if not head.startswith(_PDF_SIGNATURE):
        raise InvalidPdfSignatureError()

    digest = sha256()
    digest.update(head)
    size_bytes = len(head)

    while True:
        chunk = await upload.read(_HASH_CHUNK_BYTES)
        if not chunk:
            break
        size_bytes += len(chunk)
        if size_bytes > MAX_UPLOAD_BYTES:
            raise OversizedUploadError()
        digest.update(chunk)

    return ValidatedUpload(
        filename=display_name,
        content_type=_SUPPORTED_CONTENT_TYPE,
        size_bytes=size_bytes,
        sha256=digest.hexdigest(),
    )
