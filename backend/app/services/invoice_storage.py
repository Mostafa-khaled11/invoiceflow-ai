"""Private local storage for validated invoice PDFs.

Files live under a configured storage root that is never exposed through
static serving. Physical keys are generated internally from UUIDs and the
original client filename is kept as metadata only, so no user input ever
reaches a filesystem path. The class boundaries are intentionally narrow so
a later object-storage backend can replace them without touching business
logic.
"""

import os
from pathlib import Path
from typing import BinaryIO, Final
from uuid import uuid4

_CHUNK_BYTES: Final[int] = 1024 * 1024
_STAGING_DIRNAME: Final[str] = ".staging"
_STAGING_SUFFIX: Final[str] = ".part"


class InvoiceStorageError(Exception):
    """Raised when a storage operation cannot be completed safely."""


class InvoiceStorage:
    """Private filesystem storage for invoice PDF bytes."""

    def __init__(self, root: Path) -> None:
        """Prepare the storage root and its staging area."""

        self.root = root
        self._staging = root / _STAGING_DIRNAME
        for directory in (self.root, self._staging):
            directory.mkdir(parents=True, exist_ok=True)

    def stage_upload(self, payload: BinaryIO) -> Path:
        """Copy upload bytes into the private staging directory.

        On write failure the partial file is removed so failed staging never
        leaves debris, and a generic error is raised without filesystem
        details.
        """

        staged = self._staging / f"{uuid4().hex}{_STAGING_SUFFIX}"
        try:
            payload.seek(0)
            with staged.open("wb") as sink:
                while True:
                    chunk = payload.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    sink.write(chunk)
        except OSError as exc:
            staged.unlink(missing_ok=True)
            raise InvoiceStorageError("staging the upload failed") from exc
        return staged

    def commit_staged(self, staged: Path, key: str) -> Path:
        """Move a staged file to its final private location atomically.

        A key that already exists is treated as an unrecoverable collision:
        the call refuses to overwrite silently and the caller is responsible
        for discarding the staged file.
        """

        final = self._resolve(key)
        if final.exists():
            raise InvoiceStorageError("storage key collision")
        os.replace(staged, final)
        return final

    def discard(self, staged: Path) -> None:
        """Delete a staged file if it still exists."""

        staged.unlink(missing_ok=True)

    def abandon(self, staged: Path, key: str) -> None:
        """Clean up after a failed commit: drop staged and moved files.

        Used by the persistence flow before it rolls back, so neither the
        staging area nor the final storage keeps orphaned bytes.
        """

        self.discard(staged)
        self._resolve(key).unlink(missing_ok=True)

    def _resolve(self, key: str) -> Path:
        """Resolve a storage key inside the root, rejecting unsafe forms."""

        if not key or Path(key).name != key:
            raise InvoiceStorageError("unsafe storage key")
        root = self.root.resolve()
        candidate = (root / key).resolve()
        if candidate.parent != root:
            raise InvoiceStorageError("unsafe storage key")
        return candidate
