"""Binary base/delta format and read-only memory-mapped searches."""

import hashlib
import mmap
import struct
from dataclasses import dataclass

BASE_MAGIC = b"PULPXIDX"
DELTA_MAGIC = b"PULPXDLT"
FORMAT_VERSION = 1
HEADER = struct.Struct(">8sB7s")
COUNTS = struct.Struct(">QQ")
RECORD = struct.Struct(">16s32sQq")
HASH_SIZE = 16


class InvalidIndex(ValueError):
    """The file cannot establish either presence or absence of a path."""


def path_hash(path):
    """Hash the exact distribution-relative path, without URL normalization."""
    return hashlib.sha256(path.encode("utf-8")).digest()[:HASH_SIZE]


@dataclass(frozen=True, slots=True)
class Entry:
    path_hash: bytes
    artifact_sha256: bytes
    size: int
    last_modified: int

    def __post_init__(self):
        if not isinstance(self.path_hash, bytes) or len(self.path_hash) != HASH_SIZE:
            raise ValueError("A path hash must contain exactly 16 bytes")
        if not isinstance(self.artifact_sha256, bytes) or len(self.artifact_sha256) != 32:
            raise ValueError("An artifact digest must contain exactly 32 bytes")
        if type(self.size) is not int or not 0 <= self.size < 2**64:
            raise ValueError("Size must be an unsigned 64-bit integer")
        if type(self.last_modified) is not int or not -(2**63) <= self.last_modified < 2**63:
            raise ValueError("Last-Modified must be a signed 64-bit integer")

    @classmethod
    def for_path(cls, path, artifact_sha256, size, last_modified):
        return cls(path_hash(path), bytes.fromhex(artifact_sha256), size, last_modified)

    def pack(self):
        return RECORD.pack(self.path_hash, self.artifact_sha256, self.size, self.last_modified)


def artifact_entries(path, artifact_sha256, size, last_modified):
    """Include the directory alias of an index.html artifact, including root."""
    yield Entry.for_path(path, artifact_sha256, size, last_modified)
    if path == "index.html" or path.endswith("/index.html"):
        yield Entry.for_path(path[: -len("index.html")], artifact_sha256, size, last_modified)


class Segment:
    """An immutable file. Keep open for repeated lookups, then close explicitly.

    Loading validates structure, not every payload byte. Only trusted writer output
    should be opened; use ``verify`` for a full digest/order audit. Files must never
    be truncated or modified while mapped.
    """

    def __init__(self, path):
        self._mapping = None
        with open(path, "rb") as stream:
            prefix = stream.read(HEADER.size)
            if len(prefix) != HEADER.size:
                raise InvalidIndex("Truncated header")
            magic, version, reserved = HEADER.unpack(prefix)
            if magic not in (BASE_MAGIC, DELTA_MAGIC):
                raise InvalidIndex("Unknown index magic")
            if version != FORMAT_VERSION or reserved != bytes(7):
                raise InvalidIndex("Unsupported format version or flags")
            self.kind = "base" if magic == BASE_MAGIC else "delta"
            self.record_offset = HEADER.size
            if self.kind == "delta":
                raw_counts = stream.read(COUNTS.size)
                if len(raw_counts) != COUNTS.size:
                    raise InvalidIndex("Truncated delta counts")
                self.count, self.deleted_count = COUNTS.unpack(raw_counts)
                self.record_offset += COUNTS.size
            else:
                self.count = self.deleted_count = 0
            length = stream.seek(0, 2)
            if self.kind == "base":
                self.count, remainder = divmod(length - self.record_offset, RECORD.size)
                if remainder:
                    raise InvalidIndex("Truncated base record")
            expected_length = (
                self.record_offset + self.count * RECORD.size + self.deleted_count * HASH_SIZE
            )
            if length != expected_length:
                raise InvalidIndex("Delta counts do not match file size")
            self.byte_size = length
            self.deleted_offset = self.record_offset + self.count * RECORD.size
            self._mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)

    def close(self):
        if self._mapping is not None:
            self._mapping.close()
            self._mapping = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _data(self):
        if self._mapping is None:
            raise ValueError("Index is closed")
        return self._mapping

    def _find(self, key, offset, width, count):
        data = self._data()
        low, high = 0, count
        while low < high:
            middle = (low + high) // 2
            start = offset + middle * width
            found = data[start : start + HASH_SIZE]
            if found < key:
                low = middle + 1
            else:
                high = middle
        start = offset + low * width
        if low < count and data[start : start + HASH_SIZE] == key:
            return start
        return None

    def lookup(self, key):
        """Return (matched, entry); a matched None is an explicit deletion."""
        if not isinstance(key, bytes) or len(key) != HASH_SIZE:
            raise ValueError("Lookup requires a 16-byte path hash")
        offset = self._find(key, self.record_offset, RECORD.size, self.count)
        if offset is not None:
            return True, Entry(*RECORD.unpack_from(self._data(), offset))
        if self._find(key, self.deleted_offset, HASH_SIZE, self.deleted_count) is not None:
            return True, None
        return False, None

    def entries(self):
        data = self._data()
        for index in range(self.count):
            yield Entry(*RECORD.unpack_from(data, self.record_offset + index * RECORD.size))

    def deletions(self):
        data = self._data()
        for index in range(self.deleted_count):
            start = self.deleted_offset + index * HASH_SIZE
            yield data[start : start + HASH_SIZE]

    def verify(self, expected_digest):
        """Audit the entire payload. This is O(file size), not a request operation."""
        if hashlib.sha256(self._data()).hexdigest() != expected_digest:
            raise InvalidIndex("Segment digest mismatch")
        previous = None
        for entry in self.entries():
            if previous is not None and entry.path_hash <= previous:
                raise InvalidIndex("Unsorted or duplicate record key")
            previous = entry.path_hash
        previous = None
        for key in self.deletions():
            if previous is not None and key <= previous:
                raise InvalidIndex("Unsorted or duplicate deletion key")
            if self._find(key, self.record_offset, RECORD.size, self.count) is not None:
                raise InvalidIndex("A delta both replaces and deletes a key")
            previous = key
