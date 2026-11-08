"""BlobStore: where recording bytes live (telemetry §7).

Absorbs the storage backend: today a content-addressed directory tree at
`<root>/sha256/ab/cd/<hash>`; later an S3-compatible store (Garage) behind the same Protocol.
Writes stream to a temp file, verify the sha256 while writing, and rename atomically, so a
half-written or corrupted upload never becomes a blob.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol

CHUNK_SIZE = 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BlobError(Exception):
    """Base class for blob store errors."""


class InvalidHash(BlobError):
    """The given hash is not 64 lowercase hex characters."""


class HashMismatch(BlobError):
    """The bytes received don't hash to the sha256 the caller declared."""

    def __init__(self, expected: str, actual: str) -> None:
        super().__init__(f"sha256 mismatch: expected {expected}, got {actual}")
        self.expected = expected
        self.actual = actual


class BlobNotFound(BlobError):
    """No blob is stored under this hash."""


class BlobStore(Protocol):
    def put(self, sha256: str, stream: BinaryIO) -> int:
        """Store the stream under its hash; return its size. Safe to repeat."""
        ...

    def get(self, sha256: str) -> BinaryIO:
        """Open the blob for reading. Raises BlobNotFound."""
        ...

    def exists(self, sha256: str) -> bool: ...

    def size(self, sha256: str) -> int:
        """Size in bytes. Raises BlobNotFound."""
        ...

    def delete(self, sha256: str) -> bool:
        """Remove the blob; return whether it existed (for the disk budget)."""
        ...


def _check_hash(sha256: str) -> str:
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        raise InvalidHash(f"not a lowercase hex sha256: {sha256!r}")
    return sha256


class FsBlobStore:
    """Content-addressed files on the local filesystem."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def path_for(self, sha256: str) -> Path:
        h = _check_hash(sha256)
        return self.root / "sha256" / h[:2] / h[2:4] / h

    def put(self, sha256: str, stream: BinaryIO) -> int:
        final = self.path_for(sha256)
        if final.exists():
            # Idempotent re-put: the bytes are already here under their hash. Drain nothing.
            return final.stat().st_size
        final.parent.mkdir(parents=True, exist_ok=True)
        # Temp file in the same directory, so the rename is atomic (same filesystem).
        fd, tmp_name = tempfile.mkstemp(dir=final.parent, prefix=".tmp-", suffix=".part")
        tmp = Path(tmp_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := stream.read(CHUNK_SIZE):
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
                out.flush()
                os.fsync(out.fileno())
            actual = digest.hexdigest()
            if actual != sha256:
                raise HashMismatch(sha256, actual)
            os.replace(tmp, final)
        finally:
            tmp.unlink(missing_ok=True)
        return size

    def get(self, sha256: str) -> BinaryIO:
        path = self.path_for(sha256)
        try:
            return path.open("rb")
        except FileNotFoundError:
            raise BlobNotFound(sha256) from None

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def size(self, sha256: str) -> int:
        try:
            return self.path_for(sha256).stat().st_size
        except FileNotFoundError:
            raise BlobNotFound(sha256) from None

    def delete(self, sha256: str) -> bool:
        path = self.path_for(sha256)
        if not path.exists():
            return False
        path.unlink()
        return True


def build_blobstore(blob_dir: str | os.PathLike[str]) -> BlobStore:
    """Factory called with settings.BLOB_DIR. Only the filesystem store exists in v0."""
    return FsBlobStore(blob_dir)
