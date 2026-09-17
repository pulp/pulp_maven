"""Binary format and bounded-sort fixtures, without Django or a database."""

import hashlib
import io
import random
import struct
from dataclasses import replace

import pytest

from pulp_maven.app.path_index.build import sorted_entries, write_base, write_delta
from pulp_maven.app.path_index.format import (
    Entry,
    InvalidIndex,
    Segment,
    artifact_entries,
    path_hash,
)


def entry(path, value=1):
    return Entry.for_path(path, f"{value:064x}", value, 1700000000 + value)


def test_exact_byte_fixture(tmp_path):
    item = Entry(bytes(range(16)), bytes(range(32)), 0x0102030405060708, -2)
    stream = io.BytesIO()
    write_base(stream, [item])
    expected = (
        b"PULPXIDX\x01"
        + bytes(7)
        + bytes(range(16))
        + bytes(range(32))
        + bytes.fromhex("0102030405060708fffffffffffffffe")
    )
    assert len(expected) == 80
    assert stream.getvalue() == expected
    path = tmp_path / "fixture.bin"
    path.write_bytes(expected)
    with Segment(path) as segment:
        assert segment.lookup(item.path_hash) == (True, item)
        segment.verify(hashlib.sha256(expected).hexdigest())


@pytest.mark.parametrize("path", ["", "index.html", "org/日本語/é.jar", "a//b", "a%2Fb"])
def test_explicit_utf8_hashing(path):
    assert path_hash(path) == hashlib.sha256(path.encode("utf-8")).digest()[:16]


@pytest.mark.parametrize("path", ["index.html", "com/example/index.html", "com/日本語/index.html"])
def test_directory_alias_has_identical_artifact_metadata(path):
    items = list(artifact_entries(path, "ab" * 32, 123, 1700000000))
    assert len(items) == 2
    assert items[0].path_hash == path_hash(path)
    assert items[1].path_hash == path_hash(path[: -len("index.html")])
    assert replace(items[1], path_hash=items[0].path_hash) == items[0]


def test_non_index_artifact_has_no_alias():
    assert len(list(artifact_entries("lib.jar", "ab" * 32, 123, 1700000000))) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"path_hash": b"short"},
        {"artifact_sha256": b"short"},
        {"size": -1},
        {"size": 2**64},
        {"size": True},
        {"last_modified": 2**63},
        {"last_modified": -(2**63) - 1},
    ],
)
def test_invalid_entry_values(change):
    with pytest.raises(ValueError):
        replace(entry("a"), **change)


def test_external_sort_multiple_merge_passes(tmp_path):
    items = [entry(str(index)) for index in range(71)]
    random.Random(42).shuffle(items)
    result = list(sorted_entries(iter(items), directory=tmp_path, chunk_size=2, fan_in=2))
    assert result == sorted(items, key=lambda item: item.path_hash)
    assert not list(tmp_path.iterdir())


def test_duplicate_hash_rejected_and_sort_runs_cleaned(tmp_path):
    items = [entry("duplicate"), entry("another"), entry("duplicate", 2)]
    with pytest.raises(InvalidIndex, match="Duplicate"):
        list(sorted_entries(items, directory=tmp_path, chunk_size=1, fan_in=2))
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"PULPXIDX",
        b"WRONGIDX\x01" + bytes(7),
        b"PULPXIDX\x02" + bytes(7),
        b"PULPXIDX\x01" + bytes(6) + b"\x01",
        b"PULPXIDX\x01" + bytes(8),
        b"PULPXDLT\x01" + bytes(7),
        b"PULPXDLT\x01" + bytes(7) + struct.pack(">QQ", 2**64 - 1, 0),
    ],
)
def test_malformed_files_are_unavailable(tmp_path, raw):
    path = tmp_path / "bad.bin"
    path.write_bytes(raw)
    with pytest.raises(InvalidIndex):
        Segment(path)


def test_delta_byte_layout(tmp_path):
    first, second = sorted([entry("a"), entry("b")], key=lambda item: item.path_hash)
    stream = io.BytesIO()
    write_delta(stream, [(first.path_hash, None), (second.path_hash, second)])
    raw = stream.getvalue()
    assert raw[:16] == b"PULPXDLT\x01" + bytes(7)
    assert raw[16:32] == struct.pack(">QQ", 1, 1)
    assert raw[32:96] == second.pack()
    assert raw[96:] == first.path_hash
    path = tmp_path / "delta.bin"
    path.write_bytes(raw)
    with Segment(path) as segment:
        assert segment.lookup(first.path_hash) == (True, None)
        assert segment.lookup(second.path_hash) == (True, second)
        segment.verify(hashlib.sha256(raw).hexdigest())
