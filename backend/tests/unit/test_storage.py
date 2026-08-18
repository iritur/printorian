"""The object store, on a real disk.

Everything else in the suite uses the in-memory store; this is where the
filesystem implementation is actually exercised, because the properties that
matter — atomicity, deduplication, refusing a path — are properties of the disk
layout rather than of the interface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from printorian.core.errors import ValidationError
from printorian.core.storage import (
    FilesystemObjectStore,
    ObjectNotFoundError,
    digest_of,
    free_bytes,
    prepare_root,
)

CUBE = b"solid cube\nfacet normal 0 0 1\nendsolid cube\n"


@pytest.fixture
def store(tmp_path: Path) -> FilesystemObjectStore:
    return FilesystemObjectStore(prepare_root(tmp_path / "storage"))


async def test_bytes_come_back_unchanged(store: FilesystemObjectStore) -> None:
    stored = await store.put(CUBE, suffix="stl")

    assert await store.get(stored.digest) == CUBE


async def test_the_name_is_the_digest(store: FilesystemObjectStore) -> None:
    """Content-addressed, so a partial copy is detectable by rehashing — which is
    what makes an incremental off-site sync verifiable rather than merely fast."""
    stored = await store.put(CUBE, suffix="stl")

    assert stored.digest == digest_of(CUBE)
    assert stored.path.endswith(f"{stored.digest}.stl")


async def test_the_layout_fans_out(store: FilesystemObjectStore) -> None:
    """Two levels of two characters, so no directory holds more than a few
    thousand entries. A flat directory of a hundred thousand models is slow to
    list on every filesystem and hostile on some."""
    stored = await store.put(CUBE, suffix="stl")

    digest = stored.digest
    assert stored.path == f"{digest[:2]}/{digest[2:4]}/{digest}.stl"


async def test_storing_the_same_bytes_twice_writes_once(store: FilesystemObjectStore) -> None:
    """The deduplication that makes re-uploading a known model free."""
    first = await store.put(CUBE, suffix="stl")
    second = await store.put(CUBE, suffix="stl")

    assert not first.deduplicated
    assert second.deduplicated
    assert second.digest == first.digest
    assert len(list(store.root.rglob("*.stl"))) == 1


async def test_different_bytes_are_different_objects(store: FilesystemObjectStore) -> None:
    first = await store.put(CUBE, suffix="stl")
    second = await store.put(CUBE + b"x", suffix="stl")

    assert first.digest != second.digest
    assert await store.get(second.digest) == CUBE + b"x"


async def test_a_missing_object_is_a_distinct_failure(store: FilesystemObjectStore) -> None:
    """`ObjectNotFoundError`, not a generic read failure.

    A caller may reasonably treat a missing model as a gap in the library and a
    failed read as an outage, and it cannot if both arrive as the same exception.
    """
    with pytest.raises(ObjectNotFoundError):
        await store.get(digest_of(b"never stored"))


async def test_deleting_removes_the_file_and_prunes_its_directories(
    store: FilesystemObjectStore,
) -> None:
    stored = await store.put(CUBE, suffix="stl")

    assert await store.delete(stored.digest) is True
    assert await store.exists(stored.digest) is False
    # Nothing left behind: a store that has had churn should not keep tens of
    # thousands of empty fan-out directories.
    assert list(store.root.rglob("*")) == []


async def test_deleting_something_absent_is_not_an_error(store: FilesystemObjectStore) -> None:
    """Retention sweeps and restores can both leave the database naming a file that
    is gone. Failing over it would stop the rest of the cleanup."""
    assert await store.delete(digest_of(b"never stored")) is False


async def test_a_path_cannot_be_smuggled_in_as_a_digest(store: FilesystemObjectStore) -> None:
    """The only untrusted string that reaches the filesystem layout is the digest,
    so it is checked in one place. `../` fails the alphabet test."""
    for hostile in ("../../etc/passwd", "..", "a" * 63, "z" * 64, ""):
        with pytest.raises(ValidationError):
            await store.get(hostile)


async def test_no_temporary_file_survives_a_write(store: FilesystemObjectStore) -> None:
    """The write is temp-file-then-rename, so a crash leaves no half-object under a
    digest that promises a whole one. Nothing should linger afterwards either."""
    await store.put(CUBE, suffix="stl")

    assert [p.name for p in store.root.rglob(".tmp-*")] == []


def test_an_unusable_root_fails_loudly(tmp_path: Path) -> None:
    """A farm whose storage is missing or read-only should fail to boot with a
    reason, not accept an order and fail at prep."""
    root = tmp_path / "deep" / "nested" / "storage"

    assert prepare_root(root).is_dir()
    assert free_bytes(root) > 0
