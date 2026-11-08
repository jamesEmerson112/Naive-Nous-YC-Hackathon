"""BlobStore: where recording bytes live (telemetry §7).

Absorbs the storage backend: today a content-addressed directory tree at
`<root>/sha256/ab/cd/<hash>`; later an S3-compatible store (Garage) behind the same Protocol.
Writes stream to a temp file under `<root>/incoming/`, verify the sha256 while writing, and
rename atomically, so a half-written or corrupted upload never becomes a blob. `writer()`
exposes that temp file to callers that receive bytes in chunks (a streamed HTTP upload), so
a large upload is written to disk once.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol

CHUNK_SIZE = 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


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


class BlobTooLarge(BlobError):
    """More bytes arrived than the writer's `max_bytes` allows."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"blob larger than {max_bytes} bytes")
        self.max_bytes = max_bytes


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

    def writer(self, max_bytes: int | None = None) -> BlobWriter:
        """A temp file in the store to write chunks into, then `commit(sha256)`."""
        ...


def _check_hash(sha256: str) -> str:
    if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
        raise InvalidHash(f"not a lowercase hex sha256: {sha256!r}")
    return sha256


class BlobWriter:
    """A temp file inside the store, hashed and counted as chunks arrive.

    `commit(sha256)` fsyncs, checks the hash and renames the file into place (or drops it if
    that blob already exists). Leaving the `with` block without committing deletes it.
    """

    def __init__(self, store: FsBlobStore, max_bytes: int | None = None) -> None:
        self._store = store
        self.max_bytes = max_bytes
        incoming = store.root / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        # Same filesystem as the blobs, so the final rename is atomic.
        fd, name = tempfile.mkstemp(dir=incoming, prefix=".tmp-", suffix=".part")
        self._tmp = Path(name)
        self._out = os.fdopen(fd, "wb")
        self._digest = hashlib.sha256()
        self.size = 0

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.max_bytes is not None and self.size > self.max_bytes:
            raise BlobTooLarge(self.max_bytes)
        self._digest.update(chunk)
        self._out.write(chunk)

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()

    def commit(self, sha256: str) -> int:
        """Keep the bytes under `sha256` and return their size. Raises HashMismatch."""
        final = self._store.path_for(sha256)
        self._out.flush()
        os.fsync(self._out.fileno())
        self._out.close()
        if self.sha256 != sha256:
            raise HashMismatch(sha256, self.sha256)
        if final.exists():
            return final.stat().st_size  # already stored under its hash
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self._tmp, final)
        return self.size

    def close(self) -> None:
        self._out.close()
        self._tmp.unlink(missing_ok=True)

    def __enter__(self) -> BlobWriter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class FsBlobStore:
    """Content-addressed files on the local filesystem."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root)

    def path_for(self, sha256: str) -> Path:
        h = _check_hash(sha256)
        return self.root / "sha256" / h[:2] / h[2:4] / h

    def writer(self, max_bytes: int | None = None) -> BlobWriter:
        return BlobWriter(self, max_bytes)

    def put(self, sha256: str, stream: BinaryIO) -> int:
        final = self.path_for(sha256)
        if final.exists():
            # Idempotent re-put: the bytes are already here under their hash. Drain nothing.
            return final.stat().st_size
        with self.writer() as w:
            while chunk := stream.read(CHUNK_SIZE):
                w.write(chunk)
            return w.commit(sha256)

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
