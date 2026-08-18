"""Where the big files live.

Model meshes and prepared plates are tens of megabytes each. They belong on a disk,
not in PostgreSQL: inline bytes bloat every dump, stretch every restore, and make
the one artifact a recovery depends on slower to move at exactly the moment that
matters. The database stores a reference and a digest; this stores the bytes
(ARCHITECTURE §10).

**Content-addressed.** The name of an object *is* the SHA-256 of its contents, so:

* re-uploading the same file costs nothing — the write is a no-op and the caller
  gets the same key back;
* a partial or corrupted copy is detectable by rehashing, which makes an
  incremental off-site sync verifiable rather than merely fast;
* no caller can ever hand in a path. Every method takes a digest, and the layout is
  derived here, so path traversal is impossible by construction rather than by
  validation.

The two-level fan-out (``ab/cd/abcd…``) keeps any one directory to a few thousand
entries. A single flat directory of a hundred thousand models is slow to list on
every filesystem and hostile on some.

:class:`ObjectStore` is a Protocol so the local disk can become S3-compatible
storage later without the contexts above it noticing — the swap ARCHITECTURE §10
anticipates. Nothing here knows what is in the bytes.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from printorian.core.errors import IntegrationError, ValidationError

#: Length of a hex-encoded SHA-256. Anything else is not a digest this produced.
_DIGEST_LENGTH = 64

#: Characters allowed in a digest. Checked before a digest reaches the filesystem.
_DIGEST_ALPHABET = frozenset("0123456789abcdef")

#: How many characters of the digest form each directory level.
_FANOUT = 2


class StorageError(IntegrationError):
    """The object store could not do what was asked."""

    code = "error.storage"


class ObjectNotFoundError(StorageError):
    """No object with that digest. Distinct from a read failure — the caller may
    reasonably treat a missing model as a gap in the library and a failed read as
    an outage."""

    code = "error.storage.not_found"


def digest_of(data: bytes) -> str:
    """The content address of these bytes."""
    return hashlib.sha256(data).hexdigest()


def validate_digest(digest: str) -> str:
    """Normalise and check a digest before it is used to build a path.

    The single reason this exists: a digest is the *only* untrusted string that
    reaches the filesystem layout, so it is checked in one place rather than at
    each call site. ``../../etc/passwd`` fails the alphabet test.
    """
    normalised = digest.strip().lower()
    if len(normalised) != _DIGEST_LENGTH or not set(normalised) <= _DIGEST_ALPHABET:
        raise ValidationError("error.storage.invalid_digest", digest=digest[:80])
    return normalised


@dataclass(frozen=True, slots=True)
class StoredObject:
    """What a write produced."""

    digest: str
    #: Relative to the store's root, and stored on the row that references it.
    #: Relative rather than absolute so moving the farm's storage directory — or
    #: restoring onto a box with a different layout — does not invalidate every
    #: row in the database.
    path: str
    size_bytes: int
    #: True when the object was already present. The caller can then skip the work
    #: that would only have produced what is already there.
    deduplicated: bool = False


@runtime_checkable
class ObjectStore(Protocol):
    """Somewhere bytes can be put and got back by their digest."""

    async def put(self, data: bytes, *, suffix: str = "") -> StoredObject: ...

    async def get(self, digest: str) -> bytes: ...

    async def exists(self, digest: str) -> bool: ...

    async def delete(self, digest: str) -> bool: ...


class FilesystemObjectStore:
    """An object store rooted at a directory on the farm's disk.

    Every method is run through :func:`asyncio.to_thread`. A 40 MB plate read on the
    event loop stalls every other request in the process for the duration, and the
    dispatcher reads one per job.
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    async def put(self, data: bytes, *, suffix: str = "") -> StoredObject:
        """Store bytes, returning where they went. Idempotent.

        The write is atomic: content goes to a temporary file in the same directory
        and is then renamed into place, so a crash mid-write leaves no half-object
        under a digest that promises whole ones. Same directory because ``rename``
        is only atomic within a filesystem.
        """
        digest = digest_of(data)
        return await asyncio.to_thread(self._put_sync, data, digest, _clean_suffix(suffix))

    async def get(self, digest: str) -> bytes:
        return await asyncio.to_thread(self._get_sync, validate_digest(digest))

    async def exists(self, digest: str) -> bool:
        return await asyncio.to_thread(lambda: self._find(validate_digest(digest)) is not None)

    async def delete(self, digest: str) -> bool:
        """Remove an object. True when something was removed.

        Deliberately tolerant of an already-absent object: retention sweeps and
        restores can both leave the database naming a file that is gone, and
        failing the sweep over it would stop the rest of the cleanup.
        """
        return await asyncio.to_thread(self._delete_sync, validate_digest(digest))

    # -- internals -------------------------------------------------------

    def _directory(self, digest: str) -> Path:
        return self._root / digest[:_FANOUT] / digest[_FANOUT : _FANOUT * 2]

    def _find(self, digest: str) -> Path | None:
        """The stored file for a digest, whatever suffix it was written with."""
        directory = self._directory(digest)
        if not directory.is_dir():
            return None
        for candidate in directory.glob(f"{digest}*"):
            if candidate.is_file():
                return candidate
        return None

    def _put_sync(self, data: bytes, digest: str, suffix: str) -> StoredObject:
        existing = self._find(digest)
        if existing is not None:
            # Identical content is already here. Writing it again would be pure
            # cost — the bytes cannot differ, because the name is their hash.
            return StoredObject(
                digest=digest,
                path=self._relative(existing),
                size_bytes=existing.stat().st_size,
                deduplicated=True,
            )

        directory = self._directory(digest)
        target = directory / f"{digest}{suffix}"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=directory, prefix=".tmp-")
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    # The rename is atomic, but only orders the *directory entry*.
                    # Without this the file can be visible and empty after a power
                    # loss, which is the one state the digest cannot warn about.
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise StorageError("error.storage.write_failed", digest=digest) from exc

        return StoredObject(digest=digest, path=self._relative(target), size_bytes=len(data))

    def _get_sync(self, digest: str) -> bytes:
        found = self._find(digest)
        if found is None:
            raise ObjectNotFoundError("error.storage.not_found", digest=digest)
        try:
            return found.read_bytes()
        except OSError as exc:
            raise StorageError("error.storage.read_failed", digest=digest) from exc

    def _delete_sync(self, digest: str) -> bool:
        found = self._find(digest)
        if found is None:
            return False
        try:
            found.unlink()
        except OSError as exc:
            raise StorageError("error.storage.delete_failed", digest=digest) from exc
        # Prune the fan-out directories once they empty, so a store that has had a
        # lot of churn does not keep tens of thousands of empty directories.
        for parent in (found.parent, found.parent.parent):
            with _ignore_os_error():
                parent.rmdir()
        return True

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._root).as_posix()


class InMemoryObjectStore:
    """The same contract, held in a dict. For tests that are not about the disk."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._suffixes: dict[str, str] = {}

    async def put(self, data: bytes, *, suffix: str = "") -> StoredObject:
        digest = digest_of(data)
        deduplicated = digest in self._objects
        if not deduplicated:
            self._objects[digest] = data
            self._suffixes[digest] = _clean_suffix(suffix)
        return StoredObject(
            digest=digest,
            path=f"{digest[:_FANOUT]}/{digest[_FANOUT : _FANOUT * 2]}/"
            f"{digest}{self._suffixes[digest]}",
            size_bytes=len(self._objects[digest]),
            deduplicated=deduplicated,
        )

    async def get(self, digest: str) -> bytes:
        found = self._objects.get(validate_digest(digest))
        if found is None:
            raise ObjectNotFoundError("error.storage.not_found", digest=digest)
        return found

    async def exists(self, digest: str) -> bool:
        return validate_digest(digest) in self._objects

    async def delete(self, digest: str) -> bool:
        return self._objects.pop(validate_digest(digest), None) is not None


def _clean_suffix(suffix: str) -> str:
    """A file extension, reduced to something safe to append to a digest.

    Kept only so a human browsing the store — or a `file` command — can tell an
    STL from a 3MF. It is never used to locate anything, so a hostile one costs
    nothing; it is sanitised anyway rather than relying on that staying true.
    """
    if not suffix:
        return ""
    cleaned = "".join(c for c in suffix.lower() if c.isalnum())[:8]
    return f".{cleaned}" if cleaned else ""


class _ignore_os_error:  # noqa: N801 - a context manager used as a statement
    """`contextlib.suppress(OSError)`, spelled locally to keep the intent obvious:
    a directory that is not empty, or that another process just removed, is not a
    failure of the delete that prompted the attempt."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


def build_object_store(root: Path | str) -> ObjectStore:
    """The store this deployment uses.

    One function so that swapping in S3-compatible storage later is a change here
    and nowhere else.
    """
    store: ObjectStore = FilesystemObjectStore(root)
    return store


def prepare_root(root: Path | str) -> Path:
    """Create the storage root if it does not exist, and prove it is writable.

    Called at startup rather than on first upload: a farm whose storage directory
    is missing, read-only or on an unmounted disk should fail to boot with a clear
    reason, not accept an order and fail at dispatch.
    """
    path = Path(root)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".writable"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise StorageError("error.storage.root_unusable", root=str(path)) from exc
    return path


def free_bytes(root: Path | str) -> int:
    """Space left on the storage disk, for the health endpoint.

    A farm that has run out of disk accepts uploads, hashes them, and fails the
    write — and the first symptom is a customer's order that cannot be prepared.
    Cheaper to notice here.
    """
    return shutil.disk_usage(Path(root)).free


__all__ = [
    "FilesystemObjectStore",
    "InMemoryObjectStore",
    "ObjectNotFoundError",
    "ObjectStore",
    "StorageError",
    "StoredObject",
    "build_object_store",
    "digest_of",
    "free_bytes",
    "prepare_root",
    "validate_digest",
]
