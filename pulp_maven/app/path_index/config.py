"""Opt-in settings and storage identity for the experimental integration."""

import hashlib
import json

from django.conf import settings

S3_BACKENDS = {"storages.backends.s3.S3Storage", "storages.backends.s3boto3.S3Boto3Storage"}


def enabled(repository):
    return repository.pulp_labels.get("path_index") == "true"


def profile(domain):
    """Do not mix indexes after a domain/storage configuration change."""
    data = {
        "revision": 3,
        "domain": str(domain.pk),
        "storage": domain.storage_class,
    }
    if domain.storage_class in S3_BACKENDS:
        storage = domain.get_storage()
        # The default domain can obtain these from legacy AWS_* settings rather
        # than STORAGES. Reading attributes does not create a client or do S3 I/O.
        data["s3"] = {
            name: getattr(storage, name)
            for name in ("bucket_name", "location", "endpoint_url", "region_name")
        }
    else:
        data["options"] = domain.storage_settings
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def store(repository):
    """Reuse the domain's S3 configuration, obtaining its client after fork."""
    domain = repository.pulp_domain
    if domain.storage_class not in S3_BACKENDS:
        raise ValueError("Maven path indexes require an S3 storage backend on the domain")
    storage = domain.get_storage()

    def object_parameters(key):
        parameters = dict(storage.get_object_parameters(key))
        if storage.default_acl:
            parameters.setdefault("ACL", storage.default_acl)
        return parameters

    from .s3 import S3IndexStore

    # Configuration changes get a fresh immutable namespace so a retained
    # version can be rebuilt without overwriting its previous manifest.
    prefix = "/".join(
        part for part in (storage.location.strip("/"), "maven-path-index", profile(domain)) if part
    )
    return S3IndexStore(
        storage.connection.meta.client,
        storage.bucket_name,
        prefix,
        settings.MAVEN_PATH_INDEX_CACHE_DIR,
        str(repository.pulp_domain_id),
        str(repository.pk),
        max_cache_bytes=settings.MAVEN_PATH_INDEX_CACHE_BYTES,
        work_directory=settings.MAVEN_PATH_INDEX_WORK_DIR,
        object_parameters=object_parameters,
    )
