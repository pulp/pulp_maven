"""Immutable manifests, durable publication, and explicit background compaction.

No database state is inferred here. The integration layer must bind manifests to
completed repository versions and coordinate retention of their shared files.
"""

import fcntl
import hashlib
import heapq
import itertools
import json
import os
import re
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from .build import merge_operations, sorted_entries, write_base, write_delta
from .format import Entry, InvalidIndex, Segment, path_hash

MANIFEST_VERSION = 1
MAX_MANIFEST_BYTES = 65536
MAX_SEGMENTS = 32
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class PublicationConflict(RuntimeError):
    """A different immutable manifest already exists for this version."""


class NeedsCompaction(RuntimeError):
    """Publishing another change file would exceed the lookup bound."""


def _uuid(value):
    try:
        valid = isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        valid = False
    if not valid:
        raise InvalidIndex("Manifest identities must be canonical UUID strings")


@dataclass(frozen=True, slots=True)
class Reference:
    digest: str
    kind: str
    byte_size: int
    start: str | None
    end: str
    level: int = 0

    def __post_init__(self):
        if not isinstance(self.digest, str) or not DIGEST.fullmatch(self.digest):
            raise InvalidIndex("Invalid segment digest")
        if self.kind not in ("base", "delta"):
            raise InvalidIndex("Unknown segment type")
        if type(self.byte_size) is not int or self.byte_size < 16:
            raise InvalidIndex("Invalid segment byte size")
        if type(self.level) is not int or not 0 <= self.level <= 63:
            raise InvalidIndex("Invalid compaction level")
        _uuid(self.end)
        if self.kind == "base":
            if self.start is not None or self.level != 0:
                raise InvalidIndex("A base has no predecessor or compaction level")
        else:
            _uuid(self.start)
            if self.start == self.end:
                raise InvalidIndex("A delta must advance the version")


@dataclass(frozen=True, slots=True)
class Manifest:
    domain_id: str
    repository_id: str
    version_id: str
    parent_id: str | None
    segments: tuple[Reference, ...]

    def __post_init__(self):
        for value in (self.domain_id, self.repository_id, self.version_id):
            _uuid(value)
        if self.parent_id is not None:
            _uuid(self.parent_id)
            if self.parent_id == self.version_id:
                raise InvalidIndex("A version cannot be its own parent")
        if not isinstance(self.segments, tuple) or not 1 <= len(self.segments) <= MAX_SEGMENTS:
            raise InvalidIndex("Invalid segment count")
        if self.segments[0].kind != "base":
            raise InvalidIndex("A manifest must start with a base")
        seen = {self.segments[0].end}
        for older, newer in itertools.pairwise(self.segments):
            if newer.kind != "delta" or newer.start != older.end or newer.end in seen:
                raise InvalidIndex("Segments do not form a contiguous, acyclic version chain")
            seen.add(newer.end)
        if self.segments[-1].end != self.version_id:
            raise InvalidIndex("Manifest does not cover its target version")
        if len(self.segments) > 1:
            if self.parent_id is None:
                raise InvalidIndex("A delta view needs a parent version")
            last = self.segments[-1]
            if last.level == 0 and last.start != self.parent_id:
                raise InvalidIndex("Latest delta does not start at the immediate parent")

    def encode(self):
        return json.dumps(
            {"format": MANIFEST_VERSION, **asdict(self)}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @classmethod
    def decode(cls, raw):
        if len(raw) > MAX_MANIFEST_BYTES:
            raise InvalidIndex("Manifest is too large")
        try:
            data = json.loads(raw)
            version = data.pop("format")
            # Reject unknown format versions rather than guessing during rolling deployments.
            if type(version) is not int or version != MANIFEST_VERSION:
                raise InvalidIndex("Unsupported manifest version")
            data["segments"] = tuple(Reference(**item) for item in data["segments"])
            return cls(**data)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            raise InvalidIndex("Invalid manifest") from exc


def _digest_file(stream):
    stream.seek(0)
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _mkdir_durable(path):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        _fsync_directory(directory.parent)


class IndexView:
    """One validated manifest's open mappings; reuse for multiple lookups."""

    def __init__(self, store, manifest, *, verify=False):
        store._check_scope(manifest)
        self.manifest = manifest
        self._stack = ExitStack()
        self.segments = []
        try:
            for reference in manifest.segments:
                segment = self._stack.enter_context(
                    Segment(store.root / "segments" / f"{reference.digest}.bin")
                )
                if segment.kind != reference.kind or segment.byte_size != reference.byte_size:
                    raise InvalidIndex("Segment does not match manifest")
                if verify:
                    segment.verify(reference.digest)
                self.segments.append(segment)
        except BaseException:
            self.close()
            raise

    def close(self):
        self._stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def lookup(self, path):
        key = path_hash(path)
        for segment in reversed(self.segments):
            matched, entry = segment.lookup(key)
            if matched:
                return entry
        return None

    def entries(self):
        for _, entry in merge_operations(self.segments):
            if entry is not None:
                yield entry


class IndexStore:
    """A single domain/repository namespace on a POSIX filesystem.

    ``update`` never reads or rewrites a base. Run ``compact`` separately and pass
    its returned checkpoint as the predecessor of future updates. Original
    version manifests stay immutable and continue to work.
    """

    def __init__(
        self,
        directory,
        domain_id,
        repository_id,
        *,
        max_segments=16,
        chunk_size=65536,
        fan_in=32,
        scratch_directory=None,
    ):
        _uuid(domain_id)
        _uuid(repository_id)
        if type(max_segments) is not int or not 2 <= max_segments <= MAX_SEGMENTS:
            raise ValueError("max_segments must be between 2 and 32")
        if chunk_size < 1 or fan_in < 2:
            raise ValueError("chunk_size must be positive and fan_in at least two")
        self.domain_id = domain_id
        self.repository_id = repository_id
        self.root = Path(directory) / domain_id / repository_id
        self.max_segments = max_segments
        self.chunk_size = chunk_size
        self.fan_in = fan_in
        self.scratch_directory = scratch_directory
        self.segment_bytes_written = 0
        self.manifest_bytes_written = 0
        for name in ("segments", "versions", "checkpoints", "staging"):
            _mkdir_durable(self.root / name)

    def _check_scope(self, manifest):
        if (manifest.domain_id, manifest.repository_id) != (self.domain_id, self.repository_id):
            raise InvalidIndex("Manifest belongs to another domain or repository")
        if len(manifest.segments) > self.max_segments:
            raise NeedsCompaction("Manifest exceeds this reader's configured segment limit")

    @contextmanager
    def _publication_lock(self):
        # This lock covers only publication, never sorting or compaction.
        # Keep its inode stable: unlinking a lock file can admit a second writer.
        with (self.root / "publish.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _install(self, temporary, destination, digest):
        with self._publication_lock():
            if destination.exists():
                with destination.open("rb") as existing:
                    if _digest_file(existing) != digest:
                        raise PublicationConflict(f"Conflicting immutable file: {destination.name}")
            else:
                os.rename(temporary, destination)
            _fsync_directory(destination.parent)

    def _write_segment(self, writer, *, kind, start, end, level=0):
        with NamedTemporaryFile(dir=self.root / "staging", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                writer(stream)
                size = stream.tell()
                self.segment_bytes_written += size
                stream.flush()
                os.fsync(stream.fileno())
                digest = _digest_file(stream)
                reference = Reference(digest, kind, size, start, end, level)
                self._install(temporary, self.root / "segments" / f"{digest}.bin", digest)
                return reference
            finally:
                temporary.unlink(missing_ok=True)

    def _write_manifest(self, manifest, *, checkpoint=False):
        self._check_scope(manifest)
        raw = manifest.encode()
        if len(raw) > MAX_MANIFEST_BYTES:
            raise InvalidIndex("Manifest is too large")
        digest = hashlib.sha256(raw).hexdigest()
        destination = (
            self.root / "checkpoints" / f"{digest}.json"
            if checkpoint
            else self.root / "versions" / f"{manifest.version_id}.json"
        )
        with NamedTemporaryFile(dir=self.root / "staging", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(raw)
                self.manifest_bytes_written += len(raw)
                stream.flush()
                os.fsync(stream.fileno())
                self._install(temporary, destination, digest)
            finally:
                temporary.unlink(missing_ok=True)
        return manifest

    def _sort(self, entries):
        return sorted_entries(
            entries,
            directory=self.scratch_directory,
            chunk_size=self.chunk_size,
            fan_in=self.fan_in,
        )

    def create(self, version_id, entries):
        """Build the initial base from a bounded, one-pass stream of entries."""
        _uuid(version_id)
        reference = self._write_segment(
            lambda stream: write_base(stream, self._sort(entries)),
            kind="base",
            start=None,
            end=version_id,
        )
        manifest = Manifest(self.domain_id, self.repository_id, version_id, None, (reference,))
        return self._write_manifest(manifest)

    def update(self, version_id, previous, entries=(), removed=()):
        """Publish changed records and deleted hashes. Upserts win replacements.

        Each input must have unique path hashes. ``previous`` must be the exact
        previous version's manifest or an equivalent compaction checkpoint.
        """
        _uuid(version_id)
        self._check_scope(previous)
        if version_id == previous.version_id:
            raise InvalidIndex("An update must have a new version identity")
        if len(previous.segments) >= self.max_segments:
            raise NeedsCompaction("Compact the predecessor before publishing another delta")

        def changed_operations():
            upserts = ((entry.path_hash, entry) for entry in self._sort(entries))
            markers = (Entry(key, bytes(32), 0, 0) for key in removed)
            deletions = ((entry.path_hash, None) for entry in self._sort(markers))
            combined = heapq.merge(upserts, deletions, key=lambda item: item[0])
            for key, group in itertools.groupby(combined, key=lambda item: item[0]):
                selected = None
                for _, entry in group:
                    if entry is not None:
                        selected = entry
                yield key, selected

        reference = self._write_segment(
            lambda stream: write_delta(
                stream, changed_operations(), directory=self.scratch_directory
            ),
            kind="delta",
            start=previous.version_id,
            end=version_id,
        )
        manifest = Manifest(
            self.domain_id,
            self.repository_id,
            version_id,
            previous.version_id,
            (*previous.segments, reference),
        )
        return self._write_manifest(manifest)

    def read_version(self, version_id):
        _uuid(version_id)
        with (self.root / "versions" / f"{version_id}.json").open("rb") as stream:
            manifest = Manifest.decode(stream.read(MAX_MANIFEST_BYTES + 1))
        self._check_scope(manifest)
        if manifest.version_id != version_id:
            raise InvalidIndex("Manifest identity differs from its filename")
        return manifest

    def read_checkpoint(self, digest):
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise InvalidIndex("Invalid checkpoint digest")
        with (self.root / "checkpoints" / f"{digest}.json").open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise InvalidIndex("Checkpoint digest mismatch")
        manifest = Manifest.decode(raw)
        self._check_scope(manifest)
        return manifest

    def open(self, manifest, *, verify=False):
        return IndexView(self, manifest, verify=verify)

    def compact(self, manifest, *, rebase=False):
        """Return and persist an equivalent checkpoint, keeping old versions valid.

        By default, merge adjacent delta runs at equal generation levels. A full
        base rewrite requires explicit ``rebase=True``. Neither mode edits existing
        data, changes a version pointer, nor holds the publication lock while merging.
        """
        self._check_scope(manifest)
        # Open every source before writing: missing input cannot become a deletion.
        with self.open(manifest) as view:
            if rebase:
                reference = self._write_segment(
                    lambda stream: write_base(stream, view.entries()),
                    kind="base",
                    start=None,
                    end=manifest.version_id,
                )
                references = [reference]
            else:
                references = [manifest.segments[0]]
                for reference in manifest.segments[1:]:
                    references.append(reference)
                    while len(references) >= 3 and references[-1].level == references[-2].level:
                        older, newer = references[-2:]
                        with ExitStack() as stack:
                            sources = [
                                stack.enter_context(
                                    Segment(self.root / "segments" / f"{item.digest}.bin")
                                )
                                for item in (older, newer)
                            ]
                            merged = self._write_segment(
                                lambda stream: write_delta(
                                    stream,
                                    merge_operations(sources),
                                    directory=self.scratch_directory,
                                ),
                                kind="delta",
                                start=older.start,
                                end=newer.end,
                                level=newer.level + 1,
                            )
                        references[-2:] = [merged]
        result = Manifest(
            manifest.domain_id,
            manifest.repository_id,
            manifest.version_id,
            manifest.parent_id,
            tuple(references),
        )
        return self._write_manifest(result, checkpoint=True)
