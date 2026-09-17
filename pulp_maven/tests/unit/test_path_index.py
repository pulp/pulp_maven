"""Standalone correctness tests for the experimental index engine."""

import hashlib
import json
import multiprocessing
import random
from unittest.mock import patch
from uuid import UUID

import pytest

from pulp_maven.app.path_index.format import (
    Entry,
    InvalidIndex,
    path_hash,
)
from pulp_maven.app.path_index.store import (
    IndexStore,
    Manifest,
    NeedsCompaction,
    PublicationConflict,
)


def identity(number):
    return str(UUID(int=number))


def entry(path, value=1):
    return Entry.for_path(path, f"{value:064x}", value, 1700000000 + value)


@pytest.fixture
def store(tmp_path):
    return IndexStore(tmp_path, identity(1), identity(2), chunk_size=3, fan_in=2)


def test_empty_base_and_search_boundaries(store):
    empty = store.create(identity(10), [])
    with store.open(empty) as view:
        assert view.lookup("") is None
        assert list(view.entries()) == []
    values = sorted([entry(str(index)) for index in range(40)], key=lambda item: item.path_hash)
    manifest = store.create(identity(11), reversed(values))
    with store.open(manifest, verify=True) as view:
        segment = view.segments[0]
        assert segment.lookup(values[0].path_hash) == (True, values[0])
        assert segment.lookup(values[-1].path_hash) == (True, values[-1])
        assert segment.lookup(bytes(16)) == (False, None)
        assert segment.lookup(b"\xff" * 16) == (False, None)
    with pytest.raises(ValueError, match="closed"):
        view.lookup("1")


def test_updates_share_base_and_preserve_old_versions(store):
    base = store.create(identity(10), [entry("old"), entry("replace"), entry("keep")])
    changed = store.update(
        identity(11),
        base,
        [entry("new"), entry("replace", 2)],
        [path_hash("old"), path_hash("replace")],
    )
    assert changed.segments[0] == base.segments[0]
    assert changed.segments[-1].byte_size == 32 + 2 * 64 + 16
    assert store.read_version(identity(11)) == changed
    with store.open(changed, verify=True) as view:
        assert view.lookup("old") is None
        assert view.lookup("replace") == entry("replace", 2)
        assert view.lookup("keep") == entry("keep")
        assert view.lookup("new") == entry("new")
    with store.open(base) as view:
        assert view.lookup("old") == entry("old")
        assert view.lookup("replace") == entry("replace")
        assert view.lookup("new") is None


def test_update_does_not_open_or_rewrite_base(store):
    base = store.create(identity(10), [entry("old")])
    path = store.root / "segments" / f"{base.segments[0].digest}.bin"
    before = path.stat()
    with patch("pulp_maven.app.path_index.store.Segment", side_effect=AssertionError("base read")):
        changed = store.update(identity(11), base, [entry("new")])
    after = path.stat()
    assert (before.st_ino, before.st_mtime_ns, before.st_size) == (
        after.st_ino,
        after.st_mtime_ns,
        after.st_size,
    )
    assert changed.segments[-1].byte_size == 96


def test_aliases_and_removal_are_explicit(store):
    base = store.create(
        identity(10), [entry("index.html"), entry(""), entry("dir/index.html"), entry("dir/")]
    )
    changed = store.update(
        identity(11), base, removed=[path_hash("dir/"), path_hash("dir/index.html")]
    )
    with store.open(changed) as view:
        assert view.lookup("") == entry("")
        assert view.lookup("dir/") is None
        assert view.lookup("dir/index.html") is None
        assert view.lookup("dir") is None


def test_compaction_preserves_deletions_and_versions(store):
    original = store.create(identity(10), [entry("a"), entry("b"), entry("c")])
    first = store.update(identity(11), original, [entry("a", 2)], [path_hash("b")])
    second = store.update(identity(12), first, [entry("b", 3)], [path_hash("a")])
    third = store.update(identity(13), second, [entry("c", 4)])
    compacted = store.compact(third)
    assert len(compacted.segments) == 3
    assert compacted.segments[0] == original.segments[0]
    assert store.read_version(identity(13)) == third
    assert store.read_checkpoint(hashlib.sha256(compacted.encode()).hexdigest()) == compacted
    for manifest in (third, compacted, store.compact(compacted, rebase=True)):
        with store.open(manifest, verify=True) as view:
            assert view.lookup("a") is None
            assert view.lookup("b") == entry("b", 3)
            assert view.lookup("c") == entry("c", 4)
    with store.open(original) as view:
        assert view.lookup("a") == entry("a")
    updated = store.update(identity(14), compacted, [entry("d")])
    with store.open(updated) as view:
        assert view.lookup("a") is None
        assert view.lookup("d") == entry("d")


def test_randomized_history_matches_dictionary(store):
    randomizer = random.Random(481)
    expected = {f"path/{index}": entry(f"path/{index}") for index in range(30)}
    manifest = store.create(identity(100), expected.values())
    retained = []
    for version in range(101, 181):
        added = {}
        for _ in range(5):
            path = f"path/{randomizer.randrange(60)}"
            added[path] = entry(path, version)
        removed = {f"path/{randomizer.randrange(60)}" for _ in range(4)}
        for path in removed:
            expected.pop(path, None)
        expected.update(added)
        manifest = store.update(
            version_id=identity(version),
            previous=manifest,
            entries=added.values(),
            removed=map(path_hash, removed),
        )
        if version % 3 == 0:
            manifest = store.compact(manifest)
        if version % 17 == 0:
            manifest = store.compact(manifest, rebase=True)
        with store.open(manifest, verify=True) as view:
            for index in range(60):
                assert view.lookup(f"path/{index}") == expected.get(f"path/{index}")
            assert list(view.entries()) == sorted(
                expected.values(), key=lambda item: item.path_hash
            )
        retained.append((manifest, dict(expected)))
    for old_manifest, old_expected in retained[::13]:
        with store.open(old_manifest) as view:
            assert list(view.entries()) == sorted(
                old_expected.values(), key=lambda item: item.path_hash
            )


def test_segment_limit_requires_compaction(tmp_path):
    store = IndexStore(tmp_path, identity(1), identity(2), max_segments=3)
    first = store.create(identity(10), [])
    second = store.update(identity(11), first, [entry("a")])
    third = store.update(identity(12), second, [entry("b")])
    with pytest.raises(NeedsCompaction):
        store.update(identity(13), third, [entry("c")])
    assert not (store.root / "versions" / f"{identity(13)}.json").exists()
    store.update(identity(13), store.compact(third), [entry("c")])


def test_missing_segment_is_unavailable_even_for_miss(store):
    manifest = store.create(identity(10), [entry("a")])
    (store.root / "segments" / f"{manifest.segments[0].digest}.bin").unlink()
    with pytest.raises(FileNotFoundError):
        store.open(manifest)


@pytest.mark.parametrize(
    "mutation", ["format", "scope", "chain", "digest", "size", "identity", "parent"]
)
def test_manifest_validation(store, mutation):
    first = store.create(identity(10), [entry("a")])
    manifest = store.update(identity(11), first, [entry("b")])
    data = json.loads(manifest.encode())
    if mutation == "format":
        data["format"] = 99
    elif mutation == "scope":
        data["repository_id"] = identity(555)
    elif mutation == "chain":
        data["segments"][1]["start"] = identity(555)
    elif mutation == "digest":
        data["segments"][0]["digest"] = "../../secret"
    elif mutation == "size":
        data["segments"][0]["byte_size"] += 1
    elif mutation == "parent":
        data["parent_id"] = identity(555)
    else:
        data["version_id"] = identity(555)
    with pytest.raises(InvalidIndex):
        with store.open(Manifest.decode(json.dumps(data).encode())):
            pass


def test_writer_failure_keeps_version_absent_and_cleans_temporary_files(store):
    def broken_source():
        yield entry("a")
        raise OSError("input failed")

    with pytest.raises(OSError, match="input failed"):
        store.create(identity(10), broken_source())
    assert not list((store.root / "versions").iterdir())
    assert not list((store.root / "staging").iterdir())


def test_publication_failure_never_exposes_partial_manifest(store):
    real_rename = __import__("os").rename

    def fail_manifest(source, destination):
        if destination.parent.name == "versions":
            raise OSError("filesystem full")
        real_rename(source, destination)

    with patch("pulp_maven.app.path_index.store.os.rename", side_effect=fail_manifest):
        with pytest.raises(OSError, match="filesystem full"):
            store.create(identity(10), [entry("a")])
    assert not list((store.root / "versions").iterdir())
    assert not list((store.root / "staging").iterdir())
    manifest = store.create(identity(10), [entry("a")])
    with store.open(manifest) as view:
        assert view.lookup("a") == entry("a")


def test_same_version_cannot_be_overwritten(store):
    first = store.create(identity(10), [entry("a")])
    assert store.create(identity(10), [entry("a")]) == first
    with pytest.raises(PublicationConflict):
        store.create(identity(10), [entry("different")])
    assert store.read_version(identity(10)) == first


def _competing_writer(directory, value, ready, start, results):
    store = IndexStore(directory, identity(1), identity(2))
    ready.put(True)
    start.wait(10)
    try:
        store.create(identity(10), [entry("a", value)])
        results.put("published")
    except PublicationConflict:
        results.put("conflict")


def test_process_writers_coordinate_publication(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, results = context.Queue(), context.Queue()
    start = context.Event()
    processes = [
        context.Process(
            target=_competing_writer, args=(str(tmp_path), value, ready, start, results)
        )
        for value in (1, 2)
    ]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            ready.get(timeout=15)
        start.set()
        assert sorted(results.get(timeout=15) for _ in processes) == ["conflict", "published"]
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
        store = IndexStore(tmp_path, identity(1), identity(2))
        with store.open(store.read_version(identity(10)), verify=True) as view:
            assert view.lookup("a") in (entry("a", 1), entry("a", 2))
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        ready.close()
        results.close()


def test_full_audit_detects_payload_corruption(store):
    manifest = store.create(identity(10), [entry("a")])
    path = store.root / "segments" / f"{manifest.segments[0].digest}.bin"
    raw = bytearray(path.read_bytes())
    raw[40] ^= 1
    path.write_bytes(raw)
    with pytest.raises(InvalidIndex, match="digest"):
        store.open(manifest, verify=True)
