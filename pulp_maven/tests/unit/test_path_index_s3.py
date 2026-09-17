"""S3 publication, shared-process cache fills, eviction, and failure recovery."""

import base64
import fcntl
import hashlib
import io
import json
import multiprocessing
import os
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest

from pulp_maven.app.path_index.format import Entry, InvalidIndex, path_hash
from pulp_maven.app.path_index.s3 import CacheFull, IndexUnavailable, S3IndexStore
from pulp_maven.app.path_index.store import Manifest, PublicationConflict


def identity(value):
    return str(UUID(int=value))


def entry(path, value=1):
    return Entry.for_path(path, f"{value:064x}", value, value)


class S3Error(Exception):
    def __init__(self, status):
        self.response = {"ResponseMetadata": {"HTTPStatusCode": status}}


class FileS3:
    """A process-safe test double; records transfers and implements create-if-absent."""

    meta = SimpleNamespace(endpoint_url="https://s3.example.test")

    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(exist_ok=True)

    @contextmanager
    def locked(self):
        with (self.root / "server.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def path(self, bucket, key):
        return self.root / hashlib.sha256(f"{bucket}/{key}".encode()).hexdigest()

    def event(self, action, key):
        with (self.root / "events").open("a") as stream:
            stream.write(json.dumps([action, key]) + "\n")

    def events(self, action):
        path = self.root / "events"
        events = (
            [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        )
        return [key for op, key in events if op == action]

    def _head(self, path):
        if not path.exists():
            raise S3Error(404)
        return {
            "ContentLength": path.stat().st_size,
            "Metadata": json.loads(path.with_suffix(".json").read_text()),
        }

    def head_object(self, *, Bucket, Key):
        with self.locked():
            return self._head(self.path(Bucket, Key))

    def put_object(
        self, *, Bucket, Key, Body, ContentLength, Metadata, ChecksumSHA256, IfNoneMatch
    ):
        assert IfNoneMatch == "*"
        raw = Body.read()
        assert len(raw) == ContentLength
        assert base64.b64encode(hashlib.sha256(raw).digest()).decode() == ChecksumSHA256
        with self.locked():
            path = self.path(Bucket, Key)
            if path.exists():
                raise S3Error(412)
            path.write_bytes(raw)
            path.with_suffix(".json").write_text(json.dumps(Metadata))
            self.event("PUT", Key)
        return {}

    def get_object(self, *, Bucket, Key):
        time.sleep(getattr(self, "download_delay", 0))
        with self.locked():
            path = self.path(Bucket, Key)
            result = self._head(path)
            self.event("GET", Key)
            return {**result, "Body": io.BytesIO(path.read_bytes())}


def make_store(client, cache, **kwargs):
    return S3IndexStore(client, "indexes", "maven", cache, identity(1), identity(2), **kwargs)


@pytest.fixture
def remote(tmp_path):
    return FileS3(tmp_path / "remote")


@pytest.fixture
def store(remote, tmp_path):
    return make_store(remote, tmp_path / "pod-a")


def test_round_trip_update_transfers_only_delta(store, remote, tmp_path):
    first = store.create(identity(10), [entry("old"), entry("keep")])
    second_pod = make_store(remote, tmp_path / "pod-b")
    second = second_pod.update(identity(11), first, [entry("new", 2)], [path_hash("old")])
    assert remote.events("GET") == []  # Even a different builder pod needs no base download.
    assert len(remote.events("PUT")) == 4  # Base, delta, two manifests.
    assert remote.events("PUT")[-1].endswith(f"versions/{identity(11)}.json")
    assert store.read_version(identity(11)) == second
    with store.open(second) as view:
        assert view.lookup("old") is None
        assert view.lookup("new") == entry("new", 2)
        assert view.lookup("keep") == entry("keep")
    gets = remote.events("GET")
    # All processes in this pod can subsequently use the cache without S3 I/O.
    with make_store(remote, store.cache_directory).open(second) as view:
        assert view.lookup("absent") is None
    assert remote.events("GET") == gets
    with second_pod.open(first) as view:
        assert view.lookup("old") == entry("old")
    assert not list(store.work_directory.iterdir())


def test_compaction_checkpoint_round_trip(store, remote, tmp_path):
    first = store.create(identity(10), [entry("a"), entry("b")])
    second = store.update(identity(11), first, [entry("b", 2)], [path_hash("a")])
    third = store.update(identity(12), second, [entry("c", 3)])
    for rebase in (False, True):
        checkpoint = store.compact(third, rebase=rebase)
        digest = hashlib.sha256(checkpoint.encode()).hexdigest()
        other = make_store(remote, tmp_path / "pod-b")
        assert other.read_checkpoint(digest) == checkpoint
        assert other.read_version(identity(12)) == third
        with other.open(checkpoint, verify=True) as view:
            assert view.lookup("a") is None
            assert view.lookup("b") == entry("b", 2)
            assert view.lookup("c") == entry("c", 3)
        fourth = other.update(identity(13 + rebase), checkpoint, [entry("d")])
        with other.open(fourth) as view:
            assert view.lookup("a") is None
            assert view.lookup("d") == entry("d")


def test_missing_manifest_is_not_cached(store):
    with pytest.raises(IndexUnavailable):
        store.read_version(identity(10))
    first = store.create(identity(10), [entry("a")])
    assert store.read_version(identity(10)) == first


def test_remote_manifest_integrity_and_scope(store, remote):
    first = store.create(identity(10), [entry("a")])
    path = remote.path(store.bucket, store._key(f"versions/{identity(10)}.json"))
    raw = path.read_bytes()
    path.write_bytes(raw + b" ")
    with pytest.raises(InvalidIndex, match="digest"):
        store.read_version(identity(10))
    foreign = Manifest(identity(3), first.repository_id, first.version_id, None, first.segments)
    path.write_bytes(foreign.encode())
    path.with_suffix(".json").write_text(
        json.dumps({"sha256": hashlib.sha256(foreign.encode()).hexdigest()})
    )
    with pytest.raises(InvalidIndex, match="another domain"):
        store.read_version(identity(10))
    gets = remote.events("GET")
    with pytest.raises(InvalidIndex, match="another domain"):
        store.open(foreign)
    assert remote.events("GET") == gets


def test_failed_publication_does_not_expose_manifest(store, remote):
    with patch.object(remote, "put_object", side_effect=S3Error(503)):
        with pytest.raises(IndexUnavailable):
            store.create(identity(10), [entry("a")])
    with pytest.raises(IndexUnavailable):
        store.read_version(identity(10))
    assert not list(store.work_directory.iterdir())
    store.create(identity(10), [entry("a")])


def test_missing_predecessor_cannot_publish_version(store, remote):
    first = store.create(identity(10), [entry("a")])
    key = store._key(f"segments/{first.segments[0].digest}.bin")
    remote.path(store.bucket, key).unlink()
    with pytest.raises(IndexUnavailable):
        store.update(identity(11), first, [entry("b")])
    with pytest.raises(IndexUnavailable):
        store.read_version(identity(11))


@pytest.mark.parametrize("status", [403, 404, 500])
def test_storage_errors_never_become_path_misses(store, remote, status):
    first = store.create(identity(10), [entry("a")])
    with patch.object(remote, "get_object", side_effect=S3Error(status)):
        with pytest.raises(IndexUnavailable):
            store.open(first)
    assert not list((store.local.root / "segments").iterdir())


def test_corrupt_download_never_becomes_visible(store, remote):
    first = store.create(identity(10), [entry("a")])
    key = store._key(f"segments/{first.segments[0].digest}.bin")
    path = remote.path(store.bucket, key)
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(InvalidIndex, match="digest"):
        store.open(first)
    assert not list((store.local.root / "segments").iterdir())
    assert not list((store.cache_directory / "downloads").iterdir())
    path.write_bytes(original)
    with store.open(first) as view:
        assert view.lookup("a") == entry("a")


def test_interrupted_download_is_retryable(store, remote):
    first = store.create(identity(10), [entry("a")])

    class BrokenBody(io.BytesIO):
        def read(self, size=-1):
            raise OSError("connection lost")

    with patch.object(
        remote, "get_object", return_value={"ContentLength": 80, "Body": BrokenBody()}
    ):
        with pytest.raises(OSError, match="connection lost"):
            store.open(first)
    assert not list((store.cache_directory / "downloads").iterdir())
    (store.cache_directory / "downloads" / "download-killed.tmp").write_bytes(b"partial")
    with store.open(first) as view:
        assert view.lookup("a") == entry("a")
    assert not list((store.cache_directory / "downloads").iterdir())


def test_cache_budget_retains_live_mappings_and_refetches_evicted_files(store, remote):
    first = store.create(identity(10), [entry("a")])
    second = store.create(identity(11), [entry("b")])
    cache = make_store(remote, store.cache_directory, max_cache_bytes=80)
    with cache.open(first) as view:
        with pytest.raises(CacheFull):
            cache.open(second)
        assert view.lookup("a") == entry("a")
    with cache.open(second) as view:
        assert view.lookup("b") == entry("b")
    assert len(list((cache.local.root / "segments").iterdir())) == 1
    with cache.open(first) as view:
        assert view.lookup("a") == entry("a")
    assert len(remote.events("GET")) == 3


def test_cache_budget_is_shared_across_repositories(store, remote):
    first = store.create(identity(10), [entry("a")])
    other = S3IndexStore(
        remote,
        "indexes",
        "maven",
        store.cache_directory,
        identity(1),
        identity(3),
        max_cache_bytes=80,
    )
    second = other.create(identity(11), [entry("b")])
    with store.open(first):
        with pytest.raises(CacheFull):
            other.open(second)
    with other.open(second):
        assert not list((store.local.root / "segments").iterdir())


def test_oversized_view_is_rejected_before_download(store, remote):
    first = store.create(identity(10), [entry("a"), entry("b")])
    cache = make_store(remote, store.cache_directory, max_cache_bytes=80)
    with pytest.raises(CacheFull):
        cache.open(first)
    assert not remote.events("GET")


def test_lock_wait_is_bounded(store, remote):
    first = store.create(identity(10), [entry("a")])
    other = make_store(remote, store.cache_directory, lock_timeout=0)
    with store._segment_lock(first.segments[0].digest).open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(IndexUnavailable, match="Timed out"):
            other.open(first)


def test_conditional_conflict_and_retry(store, remote):
    first = store.create(identity(10), [entry("a")])
    assert store.create(identity(10), [entry("a")]) == first
    with pytest.raises(PublicationConflict):
        store.create(identity(10), [entry("b")])
    original = remote.put_object
    calls = 0

    def conflict_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise S3Error(409)
        return original(**kwargs)

    with patch.object(remote, "put_object", side_effect=conflict_once):
        store.create(identity(11), [entry("c")])
    assert calls == 3  # Conflict, segment retry, manifest.


def test_writer_does_not_need_list_bucket_permission(store, remote):
    original = remote.head_object

    def head_without_listing(**kwargs):
        try:
            return original(**kwargs)
        except S3Error as exc:
            if _status_for_test(exc) == 404:
                raise S3Error(403)
            raise

    with patch.object(remote, "head_object", side_effect=head_without_listing):
        first = store.create(identity(10), [entry("a")])
        second = store.update(identity(11), first, [entry("b")])
        with store.open(second) as view:
            assert view.lookup("a") == entry("a")
            assert view.lookup("b") == entry("b")


def _status_for_test(exc):
    return exc.response["ResponseMetadata"]["HTTPStatusCode"]


def test_forbidden_existing_object_is_not_overwritten(store, remote):
    first = store.create(identity(10), [entry("a")])
    puts = remote.events("PUT")
    with patch.object(remote, "head_object", side_effect=S3Error(403)):
        with pytest.raises(IndexUnavailable):
            store.create(identity(10), [entry("a")])
        with pytest.raises(IndexUnavailable):
            store.update(identity(11), first, [entry("b")])
    assert remote.events("PUT") == puts


def _download_process(remote_root, cache_root, raw, start, output):
    client = FileS3(remote_root)
    client.download_delay = 0.2
    store = make_store(client, cache_root)
    start.wait(10)
    with store.open(Manifest.decode(raw)) as view:
        output.put(view.lookup("a").size)


def _publish_process(remote_root, cache_root, value, start, output):
    store = make_store(FileS3(remote_root), cache_root)
    start.wait(10)
    try:
        store.create(identity(10), [entry("a", value)])
        output.put("published")
    except PublicationConflict:
        output.put("conflict")


def run_processes(target, arguments):
    context = multiprocessing.get_context("spawn")
    start, output = context.Event(), context.Queue()
    children = [context.Process(target=target, args=(*args, start, output)) for args in arguments]
    try:
        for child in children:
            child.start()
        start.set()
        results = [output.get(timeout=20) for child in children]
        for child in children:
            child.join(20)
            assert child.exitcode == 0
        return results
    finally:
        for child in children:
            if child.is_alive():
                child.kill()
                child.join()
        output.close()


def test_four_content_workers_download_once_per_pod(store, remote, tmp_path):
    first = store.create(identity(10), [entry("a")])
    for pod in ("pod-a", "pod-b"):
        args = [(remote.root, tmp_path / pod, first.encode())] * 4
        assert run_processes(_download_process, args) == [1] * 4
    assert len(remote.events("GET")) == 2  # Not eight, and not zero for the second pod.


def _crashing_download(remote_root, cache_root, raw):
    client = FileS3(remote_root)
    original = client.get_object

    class CrashingBody(io.BytesIO):
        def read(self, size=-1):
            if self.tell():
                os._exit(17)
            return super().read(40)

    def get(**kwargs):
        result = original(**kwargs)
        with result["Body"] as body:
            result["Body"] = CrashingBody(body.read())
        return result

    with patch.object(client, "get_object", side_effect=get):
        make_store(client, cache_root).warm(Manifest.decode(raw))


def test_killed_downloader_releases_process_locks(store, remote):
    first = store.create(identity(10), [entry("a")])
    context = multiprocessing.get_context("spawn")
    child = context.Process(
        target=_crashing_download, args=(remote.root, store.cache_directory, first.encode())
    )
    child.start()
    try:
        child.join(20)
        assert child.exitcode == 17
    finally:
        if child.is_alive():
            child.kill()
            child.join()
    assert not list((store.local.root / "segments").iterdir())
    assert list((store.cache_directory / "downloads").glob("download-*.tmp"))
    make_store(remote, store.cache_directory, lock_timeout=0.5).warm(first)
    assert not list((store.cache_directory / "downloads").iterdir())


@pytest.mark.parametrize(
    "values,expected", [([1, 1], ["published", "published"]), ([1, 2], ["conflict", "published"])]
)
def test_publishers_in_different_pods_coordinate_through_s3(remote, tmp_path, values, expected):
    args = [(remote.root, tmp_path / f"pod-{index}", value) for index, value in enumerate(values)]
    assert sorted(run_processes(_publish_process, args)) == expected
    store = make_store(remote, tmp_path / "reader")
    with store.open(store.read_version(identity(10))) as view:
        assert view.lookup("a").size in values


def test_boto3_conditional_put_contract(store):
    boto3 = pytest.importorskip("boto3")
    from botocore.stub import Stubber

    client = boto3.client("s3", aws_access_key_id="testing", aws_secret_access_key="testing")
    raw = b"example"
    digest = hashlib.sha256(raw).hexdigest()
    body = io.BytesIO(raw)
    store.client = client
    with Stubber(client) as stubber:
        stubber.add_client_error("head_object", service_error_code="404", http_status_code=404)
        stubber.add_response(
            "put_object",
            {},
            {
                "Bucket": "indexes",
                "Key": store._key("example"),
                "Body": body,
                "ContentLength": len(raw),
                "Metadata": {"sha256": digest},
                "IfNoneMatch": "*",
                "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
            },
        )
        store._put("example", body, len(raw), digest)
        stubber.assert_no_pending_responses()
