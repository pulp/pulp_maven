"""Repository opt-in and domain-owned storage configuration."""

import hashlib
import io
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from pulp_maven.app.path_index.config import S3_BACKENDS, enabled, profile, store


@pytest.mark.parametrize("labels", [{}, {"path_index": "false"}, {"path_index": True}])
def test_only_explicit_label_enables_index(labels):
    assert not enabled(SimpleNamespace(pulp_labels=labels))
    assert enabled(SimpleNamespace(pulp_labels={"path_index": "true"}))


def repository(backend="storages.backends.s3.S3Storage"):
    client = Mock()
    client.meta.endpoint_url = "https://domain-s3.example.test"
    storage = SimpleNamespace(
        connection=SimpleNamespace(meta=SimpleNamespace(client=client)),
        bucket_name="domain-artifacts",
        location="tenant/content",
        endpoint_url=client.meta.endpoint_url,
        region_name="eu-west-1",
        default_acl="private",
        get_object_parameters=Mock(return_value={}),
    )
    domain = SimpleNamespace(
        pk=uuid4(), storage_class=backend, storage_settings={}, get_storage=lambda: storage
    )
    return SimpleNamespace(pk=uuid4(), pulp_domain=domain, pulp_domain_id=domain.pk)


@pytest.mark.parametrize("backend", sorted(S3_BACKENDS))
def test_store_reuses_domain_client_bucket_and_location(backend, tmp_path, settings):
    settings.MAVEN_PATH_INDEX_CACHE_DIR = str(tmp_path)
    repo = repository(backend)
    storage = repo.pulp_domain.get_storage()
    index = store(repo)
    assert index.client is storage.connection.meta.client
    assert index.bucket == storage.bucket_name
    assert index._key("versions/test") == (
        f"tenant/content/maven-path-index/{profile(repo.pulp_domain)}/"
        f"{repo.pulp_domain_id}/{repo.pk}/versions/test"
    )


def test_storage_identity_changes_get_new_namespaces(tmp_path, settings):
    settings.MAVEN_PATH_INDEX_CACHE_DIR = str(tmp_path)
    repo = repository()
    old = store(repo)
    # Legacy default-domain AWS_* options are reflected by the resolved storage,
    # even when Domain.storage_settings and Django STORAGES remain unchanged.
    repo.pulp_domain.get_storage().location = "moved"
    new = store(repo)
    assert old.prefix != new.prefix
    assert old.local.root != new.local.root


def test_storage_profile_does_not_access_client():
    repo = repository()
    storage = repo.pulp_domain.get_storage()
    del storage.connection
    assert len(profile(repo.pulp_domain)) == 64


def test_unsupported_domain_fails_before_client_or_disk_access():
    repo = repository("pulpcore.app.models.storage.FileSystem")
    repo.pulp_domain.get_storage = Mock(side_effect=AssertionError("accessed storage"))
    with pytest.raises(ValueError, match="S3 storage backend"):
        store(repo)
    repo.pulp_domain.get_storage.assert_not_called()


def test_domain_object_options_preserve_encryption_and_index_integrity(tmp_path, settings):
    settings.MAVEN_PATH_INDEX_CACHE_DIR = str(tmp_path)
    repo = repository()
    storage = repo.pulp_domain.get_storage()
    parameters = {
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": "test-kms-key",
        "BucketKeyEnabled": True,
        "StorageClass": "STANDARD_IA",
        "Metadata": {"team": "test", "sha256": "not-the-index-digest"},
        "ContentEncoding": "gzip",
        "ChecksumAlgorithm": "CRC32",
        "ChecksumCRC32": "incorrect",
        "IfMatch": "incorrect",
    }
    storage.get_object_parameters.return_value = parameters
    index = store(repo)
    index.client.head_object.side_effect = MissingObject()
    raw = b"index bytes"
    digest = hashlib.sha256(raw).hexdigest()
    index._put("test", io.BytesIO(raw), len(raw), digest)
    call = index.client.put_object.call_args.kwargs
    assert call["ServerSideEncryption"] == "aws:kms"
    assert call["SSEKMSKeyId"] == "test-kms-key"
    assert call["BucketKeyEnabled"] is True
    assert call["StorageClass"] == "STANDARD_IA"
    assert call["ACL"] == "private"
    assert call["Metadata"] == {"team": "test", "sha256": digest}
    assert call["IfNoneMatch"] == "*"
    assert call["ContentLength"] == len(raw)
    assert call["Body"].read() == raw
    assert "ChecksumSHA256" in call
    assert not {"ChecksumAlgorithm", "ChecksumCRC32", "ContentEncoding", "IfMatch"} & call.keys()
    assert parameters["Metadata"]["sha256"] == "not-the-index-digest"
    assert "ACL" not in parameters
    storage.get_object_parameters.assert_called_with(index._key("test"))


class MissingObject(Exception):
    response = {"ResponseMetadata": {"HTTPStatusCode": 404}}


def test_domain_customer_encryption_options_apply_to_reads(tmp_path, settings):
    settings.MAVEN_PATH_INDEX_CACHE_DIR = str(tmp_path)
    repo = repository()
    parameters = {
        "SSECustomerAlgorithm": "AES256",
        "SSECustomerKey": "test-encryption-key",
        "SSECustomerKeyMD5": "test-digest",
        "RequestPayer": "requester",
        "ExpectedBucketOwner": "123456789012",
    }
    repo.pulp_domain.get_storage().get_object_parameters.return_value = {
        **parameters,
        "StorageClass": "STANDARD",
        "Metadata": {"team": "test"},
    }
    index = store(repo)
    index._head("test")
    index._get("test")
    expected = {**parameters, "Bucket": index.bucket, "Key": index._key("test")}
    index.client.head_object.assert_called_once_with(**expected)
    index.client.get_object.assert_called_once_with(**expected)
