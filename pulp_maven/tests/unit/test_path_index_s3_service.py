"""Opt-in wire tests against a disposable S3-compatible development service.

Set PULP_PATH_INDEX_TEST_S3_ENDPOINT and test-only AWS credentials to enable.
Each test owns a fresh bucket and deletes only that bucket's contents afterward.
"""

import hashlib
import io
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import pytest

from pulp_maven.app.path_index.format import Entry, path_hash
from pulp_maven.app.path_index.s3 import S3IndexStore
from pulp_maven.app.path_index.store import PublicationConflict


@pytest.fixture
def service(tmp_path):
    endpoint = os.environ.get("PULP_PATH_INDEX_TEST_S3_ENDPOINT")
    if not endpoint:
        pytest.skip("No disposable S3 test endpoint configured")
    boto3 = pytest.importorskip("boto3")
    client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1")
    bucket = f"pulp-index-test-{uuid4()}"
    client.create_bucket(Bucket=bucket)
    domain, repository = str(uuid4()), str(uuid4())

    def factory(pod):
        return S3IndexStore(client, bucket, "indexes", tmp_path / pod, domain, repository)

    try:
        yield factory
    finally:
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=item["Key"])
        client.delete_bucket(Bucket=bucket)
        client.close()


def test_s3_wire_round_trip_compaction_and_concurrent_publication(service):
    writer = service("builder")
    original = Entry.for_path("a.jar", "ab" * 32, 100, 1700000000)
    replacement = Entry.for_path("b.jar", "cd" * 32, 200, 1700000001)
    first_id = str(uuid4())

    def create(pod):
        return service(pod).create(first_id, [original])

    with ThreadPoolExecutor(max_workers=2) as executor:
        manifests = list(executor.map(create, ["writer-a", "writer-b"]))
    assert manifests[0] == manifests[1]
    second = writer.update(str(uuid4()), manifests[0], [replacement], [path_hash("a.jar")])
    reader = service("content")
    assert reader.read_version(second.version_id) == second
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: reader.warm(second), range(4)))
    with reader.open(second) as view:
        assert view.lookup("a.jar") is None
        assert view.lookup("b.jar") == replacement
    compacted = writer.compact(second, rebase=True)
    assert reader.read_checkpoint(hashlib.sha256(compacted.encode()).hexdigest()) == compacted
    with reader.open(compacted, verify=True) as view:
        assert list(view.entries()) == [replacement]
    with pytest.raises(PublicationConflict):
        writer.create(first_id, [replacement])


def test_s3_wire_conditional_put_never_overwrites(service):
    store = service("builder")
    raw = b"first publication"
    digest = hashlib.sha256(raw).hexdigest()
    store._put("conditional-test", io.BytesIO(raw), len(raw), digest)
    # Force a stale HEAD to exercise a real HTTP 412, not just the optimization.
    existing = store._head("conditional-test")
    with patch.object(store, "_head", side_effect=[None, existing]):
        store._put("conditional-test", io.BytesIO(raw), len(raw), digest)
    changed = b"different publication"
    with patch.object(store, "_head", side_effect=[None, existing]):
        with pytest.raises(PublicationConflict):
            store._put(
                "conditional-test",
                io.BytesIO(changed),
                len(changed),
                hashlib.sha256(changed).hexdigest(),
            )


def test_s3_wire_streams_multiple_download_chunks(service):
    writer, reader = service("builder"), service("reader")
    manifest = writer.create(
        str(uuid4()),
        (Entry.for_path(f"{index}.jar", "ab" * 32, index, 1) for index in range(20000)),
    )
    assert manifest.segments[0].byte_size > 1024 * 1024
    with reader.open(manifest) as view:
        assert view.lookup("0.jar").size == 0
        assert view.lookup("19999.jar").size == 19999
        assert view.lookup("20000.jar") is None
