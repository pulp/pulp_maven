"""S3 publication and a process-coordinated, disposable POSIX download cache.

S3 is authoritative. Every pod supplies one shared local cache directory. This
synchronous engine must run off the content application's event-loop thread.
"""

import base64
import fcntl
import hashlib
import os
import time
from contextlib import ExitStack, contextmanager
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from .format import InvalidIndex, Segment
from .store import (
    DIGEST,
    MAX_MANIFEST_BYTES,
    IndexStore,
    IndexView,
    Manifest,
    PublicationConflict,
    _fsync_directory,
    _mkdir_durable,
    _uuid,
)

MAX_OBJECT_BYTES = 5 * 1024**3  # Single PutObject; multipart is deliberately not implicit.


class IndexUnavailable(RuntimeError):
    """Storage cannot establish presence or absence of an artifact."""


class CacheFull(IndexUnavailable):
    """The cache cannot fit a view without evicting files still in use."""


def _status(exc):
    return getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")


@contextmanager
def _lock(path, mode, timeout):
    # Never unlink these inodes: other processes may still hold/open them.
    with path.open("a+b") as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, mode | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise IndexUnavailable("Timed out waiting for the local index cache")
                time.sleep(0.05)
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class CachedIndexView(IndexView):
    """Keep shared file locks until all mappings in this view have closed."""

    def __init__(self, store, manifest, *, verify=False):
        store.local._check_scope(manifest)
        if sum({r.digest: r.byte_size for r in manifest.segments}.values()) > store.max_cache_bytes:
            raise CacheFull("The complete version view exceeds the cache budget")
        self._pins = ExitStack()
        try:
            # Stable order also covers overlapping views opened by different processes.
            for reference in sorted(manifest.segments, key=lambda item: item.digest):
                self._pins.enter_context(store._pin(reference))
            super().__init__(store.local, manifest, verify=verify)
        except BaseException:
            self._pins.close()
            raise

    def close(self):
        try:
            super().close()
        finally:
            self._pins.close()


class S3IndexStore:
    """An immutable S3 namespace with a shared local cache, without shared EFS.

    ``client`` is a configured boto3 S3 client (created after process fork).
    All workers using ``cache_directory`` must use the same cache budget. The
    budget spans every namespace in that directory, but excludes build scratch.
    Conditional PutObject support is required; unconditional writes are never used.
    """

    def __init__(
        self,
        client,
        bucket,
        prefix,
        cache_directory,
        domain_id,
        repository_id,
        *,
        max_cache_bytes=4 * 1024**3,
        lock_timeout=30,
        work_directory=None,
        **store_options,
    ):
        if type(max_cache_bytes) is not int or max_cache_bytes < 16 or lock_timeout < 0:
            raise ValueError("Invalid cache budget or lock timeout")
        if not bucket or not prefix.strip("/"):
            raise ValueError("A bucket and a dedicated nonempty index prefix are required")
        self.client, self.bucket = client, bucket
        self.prefix = prefix.strip("/")
        self.cache_directory = Path(cache_directory).resolve()
        self.work_directory = Path(work_directory or self.cache_directory / "builds")
        self.max_cache_bytes = max_cache_bytes
        self.lock_timeout = lock_timeout
        self.store_options = store_options
        # Separate endpoints/buckets/prefixes even when Pulp identities coincide.
        source = f"{client.meta.endpoint_url}\n{bucket}\n{self.prefix}"
        namespace = hashlib.sha256(source.encode("utf-8")).hexdigest()
        self.local = IndexStore(
            self.cache_directory / namespace, domain_id, repository_id, **store_options
        )
        _mkdir_durable(self.local.root / "locks")
        _mkdir_durable(self.cache_directory / "downloads")
        _mkdir_durable(self.work_directory)
        if self.work_directory.stat().st_dev != self.cache_directory.stat().st_dev:
            raise ValueError("Build scratch and cache must share a filesystem for compaction")

    def _key(self, relative):
        return f"{self.prefix}/{self.local.domain_id}/{self.local.repository_id}/{relative}"

    def _head(self, relative, *, for_put=False):
        try:
            return self.client.head_object(Bucket=self.bucket, Key=self._key(relative))
        except Exception as exc:
            if _status(exc) == 404:
                return None
            if for_put and _status(exc) == 403:
                # S3 hides missing keys behind 403 without ListBucket permission.
                # Only a conditional PUT can establish creation in that case;
                # reads and predecessor checks must still fail closed.
                return None
            raise IndexUnavailable("Cannot inspect an S3 index object") from exc

    @staticmethod
    def _check_object(head, size, digest):
        if head["ContentLength"] != size or head.get("Metadata", {}).get("sha256") != digest:
            raise PublicationConflict("Conflicting immutable S3 index object")

    def _put(self, relative, stream, size, digest):
        if size > MAX_OBJECT_BYTES:
            raise IndexUnavailable("Index segment exceeds the single-PUT size limit")
        # A HEAD saves duplicate transfers; the conditional PUT closes the race.
        if head := self._head(relative, for_put=True):
            self._check_object(head, size, digest)
            return
        for attempt in range(3):
            stream.seek(0)
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=self._key(relative),
                    Body=stream,
                    ContentLength=size,
                    Metadata={"sha256": digest},
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
                    IfNoneMatch="*",
                )
                return
            except Exception as exc:
                if _status(exc) == 412:
                    head = self._head(relative)
                    if head is None:
                        raise IndexUnavailable("Published index object disappeared") from exc
                    self._check_object(head, size, digest)
                    return
                if _status(exc) == 409 and attempt < 2:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise IndexUnavailable("Cannot publish an S3 index object") from exc

    def _get(self, relative):
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(relative))
        except Exception as exc:
            # In particular, 403, 404 and transport errors are not artifact misses.
            raise IndexUnavailable("Cannot download an S3 index object") from exc

    def _read_manifest(self, relative, *, version_id=None, digest=None):
        result = self._get(relative)
        with result["Body"] as body:
            raw = body.read(MAX_MANIFEST_BYTES + 1)
        actual = hashlib.sha256(raw).hexdigest()
        if (
            result["ContentLength"] != len(raw)
            or result.get("Metadata", {}).get("sha256") != actual
        ):
            raise InvalidIndex("S3 manifest length or digest mismatch")
        if digest is not None and actual != digest:
            raise InvalidIndex("S3 checkpoint digest mismatch")
        manifest = Manifest.decode(raw)
        self.local._check_scope(manifest)
        if version_id is not None and manifest.version_id != version_id:
            raise InvalidIndex("S3 manifest identity differs from its key")
        return manifest

    def read_version(self, version_id):
        """Fetch an immutable version manifest; missing versions are never cached."""
        _uuid(version_id)
        return self._read_manifest(f"versions/{version_id}.json", version_id=version_id)

    def read_checkpoint(self, digest):
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise InvalidIndex("Invalid checkpoint digest")
        return self._read_manifest(f"checkpoints/{digest}.json", digest=digest)

    @contextmanager
    def _builder(self):
        with TemporaryDirectory(prefix="pulp-path-index-", dir=self.work_directory) as directory:
            options = {"scratch_directory": directory, **self.store_options}
            yield IndexStore(directory, self.local.domain_id, self.local.repository_id, **options)

    def _publish(self, manifest, builder, *, checkpoint=False):
        # Commit the manifest only after *all* referenced objects are available.
        for reference in manifest.segments:
            relative = f"segments/{reference.digest}.bin"
            path = builder.root / relative
            if path.exists():
                with path.open("rb") as stream:
                    self._put(relative, stream, reference.byte_size, reference.digest)
            else:
                head = self._head(relative)
                if head is None:
                    raise IndexUnavailable("A predecessor segment is missing from S3")
                self._check_object(head, reference.byte_size, reference.digest)
        raw = manifest.encode()
        digest = hashlib.sha256(raw).hexdigest()
        relative = (
            f"checkpoints/{digest}.json" if checkpoint else f"versions/{manifest.version_id}.json"
        )
        self._put(relative, BytesIO(raw), len(raw), digest)
        return manifest

    def create(self, version_id, entries):
        with self._builder() as builder:
            return self._publish(builder.create(version_id, entries), builder)

    def update(self, version_id, previous, entries=(), removed=()):
        """Upload only the new delta and manifest; never download the predecessor."""
        with self._builder() as builder:
            manifest = builder.update(version_id, previous, entries, removed)
            return self._publish(manifest, builder)

    def compact(self, manifest, *, rebase=False):
        with self.open(manifest), self._builder() as builder:
            # Hard links reuse the verified cache inodes, without copying the base.
            # The scratch directory must therefore reside on the cache filesystem.
            for reference in manifest.segments:
                relative = f"segments/{reference.digest}.bin"
                os.link(self.local.root / relative, builder.root / relative)
            result = builder.compact(manifest, rebase=rebase)
            return self._publish(result, builder, checkpoint=True)

    def open(self, manifest, *, verify=False):
        return CachedIndexView(self, manifest, verify=verify)

    def warm(self, manifest):
        """Download and verify a view ahead of traffic, outside the request path."""
        with self.open(manifest):
            pass

    def _segment_lock(self, digest):
        return self.local.root / "locks" / f"{digest}.lock"

    @contextmanager
    def _pin(self, reference):
        path = self.local.root / "segments" / f"{reference.digest}.bin"
        lock_path = self._segment_lock(reference.digest)
        while True:
            with _lock(lock_path, fcntl.LOCK_SH, self.lock_timeout):
                if path.exists():
                    if path.stat().st_size != reference.byte_size:
                        raise InvalidIndex("Cached segment has an invalid length")
                    os.utime(path, None)  # Approximate LRU at view-open, never per lookup.
                    yield
                    return
            with _lock(lock_path, fcntl.LOCK_EX, self.lock_timeout):
                if not path.exists():
                    self._download(reference, path)
            # Recheck after obtaining a shared lock: eviction can win this handoff.

    def _make_room(self, incoming):
        # Caller holds the global fill lock. Other readers retain shared key locks.
        candidates = list(self.cache_directory.glob("*/*/*/segments/*.bin"))
        used = sum(path.stat().st_size for path in candidates)
        for path in sorted(candidates, key=lambda item: item.stat().st_mtime_ns):
            if used + incoming <= self.max_cache_bytes:
                break
            lock_path = path.parent.parent / "locks" / f"{path.stem}.lock"
            with lock_path.open("a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                used -= path.stat().st_size
                path.unlink()
                _fsync_directory(path.parent)
        if used + incoming > self.max_cache_bytes:
            raise CacheFull("The index cache is full of files in use")

    def prune(self):
        """Trim unused segments to the budget without interrupting open views."""
        with _lock(self.cache_directory / "fill.lock", fcntl.LOCK_EX, self.lock_timeout):
            self._make_room(0)

    def _download(self, reference, destination):
        # Serialize cold fills across the pod to bound network and temporary disk
        # usage. Holding the key lock prevents duplicate downloads of this segment.
        with _lock(self.cache_directory / "fill.lock", fcntl.LOCK_EX, self.lock_timeout):
            downloads = self.cache_directory / "downloads"
            # A killed downloader releases flock; only incomplete temp files remain.
            for abandoned in downloads.glob("download-*.tmp"):
                abandoned.unlink()
            self._make_room(reference.byte_size)
            result = self._get(f"segments/{reference.digest}.bin")
            with result["Body"] as body:
                if result["ContentLength"] != reference.byte_size:
                    raise InvalidIndex("S3 segment length mismatch")
                with NamedTemporaryFile(
                    dir=downloads, prefix="download-", suffix=".tmp", delete=False
                ) as stream:
                    temporary = Path(stream.name)
                    try:
                        remaining = reference.byte_size
                        while remaining:
                            chunk = body.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise InvalidIndex("Truncated S3 segment")
                            stream.write(chunk)
                            remaining -= len(chunk)
                        if body.read(1):
                            raise InvalidIndex("Oversized S3 segment")
                        stream.flush()
                        os.fsync(stream.fileno())
                        with Segment(temporary) as segment:
                            if segment.kind != reference.kind:
                                raise InvalidIndex("S3 segment kind mismatch")
                            segment.verify(reference.digest)
                        os.rename(temporary, destination)
                        _fsync_directory(destination.parent)
                    finally:
                        temporary.unlink(missing_ok=True)
