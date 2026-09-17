"""Bounded external sorting and ordered base/delta merges."""

import heapq
import itertools
import shutil
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

from .format import (
    BASE_MAGIC,
    COUNTS,
    DELTA_MAGIC,
    FORMAT_VERSION,
    HASH_SIZE,
    HEADER,
    RECORD,
    Entry,
    InvalidIndex,
)


def _run_entries(stream):
    while raw := stream.read(RECORD.size):
        if len(raw) != RECORD.size:
            raise InvalidIndex("Truncated temporary sort run")
        yield Entry(*RECORD.unpack(raw))


def _merge_runs(paths):
    with ExitStack() as stack:
        streams = [stack.enter_context(open(path, "rb")) for path in paths]
        yield from heapq.merge(*map(_run_entries, streams), key=lambda entry: entry.path_hash)


def sorted_entries(entries, *, directory=None, chunk_size=65536, fan_in=32):
    """Sort a one-pass iterable with bounded memory and bounded open descriptors."""
    if chunk_size < 1 or fan_in < 2:
        raise ValueError("chunk_size must be positive and fan_in at least two")
    with TemporaryDirectory(dir=directory, prefix="path-index-sort-") as workdir:
        paths = []
        serial = itertools.count()

        def write_run(items):
            path = Path(workdir) / str(next(serial))
            with path.open("wb") as stream:
                for entry in items:
                    stream.write(entry.pack())
            return path

        source = iter(entries)
        while chunk := list(itertools.islice(source, chunk_size)):
            chunk.sort(key=lambda entry: entry.path_hash)
            paths.append(write_run(chunk))
        while len(paths) > fan_in:
            next_paths = []
            for start in range(0, len(paths), fan_in):
                group = paths[start : start + fan_in]
                next_paths.append(write_run(_merge_runs(group)))
                for path in group:
                    path.unlink()
            paths = next_paths
        previous = None
        for entry in _merge_runs(paths):
            if previous == entry.path_hash:
                raise InvalidIndex("Duplicate path hash: resolve replacements/collisions first")
            previous = entry.path_hash
            yield entry


def write_base(stream, entries):
    """Write strictly ordered live entries. Caller owns durability/publication."""
    stream.write(HEADER.pack(BASE_MAGIC, FORMAT_VERSION, bytes(7)))
    previous = None
    for entry in entries:
        if previous is not None and entry.path_hash <= previous:
            raise InvalidIndex("Base keys must be strictly increasing")
        stream.write(entry.pack())
        previous = entry.path_hash


def operations(segment):
    """Read one segment in key order, representing deletions as None."""
    yield from heapq.merge(
        ((entry.path_hash, entry) for entry in segment.entries()),
        ((key, None) for key in segment.deletions()),
        key=lambda operation: operation[0],
    )


def merge_operations(segments):
    """Merge oldest-to-newest segments, with the newest operation winning."""

    def numbered(index, segment):
        for key, entry in operations(segment):
            yield key, index, entry

    streams = [numbered(index, segment) for index, segment in enumerate(segments)]
    # heapq's key need not compare Entry instances when keys are repeated.
    merged = heapq.merge(*streams, key=lambda operation: operation[0])
    for key, group in itertools.groupby(merged, key=lambda operation: operation[0]):
        newest = max(group, key=lambda operation: operation[1])
        yield key, newest[2]


def write_delta(stream, ordered_operations, *, directory=None):
    """Write disjoint upsert/deletion sections from strictly ordered operations."""
    stream.write(HEADER.pack(DELTA_MAGIC, FORMAT_VERSION, bytes(7)))
    stream.write(COUNTS.pack(0, 0))
    previous = None
    count = deleted = 0
    with TemporaryFile(dir=directory) as tombstones:
        for key, entry in ordered_operations:
            if not isinstance(key, bytes) or len(key) != HASH_SIZE:
                raise InvalidIndex("Invalid deletion key")
            if previous is not None and key <= previous:
                raise InvalidIndex("Delta keys must be strictly increasing")
            if entry is None:
                tombstones.write(key)
                deleted += 1
            else:
                if entry.path_hash != key:
                    raise InvalidIndex("Operation key differs from entry key")
                stream.write(entry.pack())
                count += 1
            previous = key
        tombstones.seek(0)
        shutil.copyfileobj(tombstones, stream, length=1024 * 1024)
    stream.seek(HEADER.size)
    stream.write(COUNTS.pack(count, deleted))
    stream.seek(0, 2)
