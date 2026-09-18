"""Opt-in settings and storage identity for the experimental integration."""

import hashlib
import json

from django.conf import settings


def enabled(repository):
    return (
        getattr(settings, "MAVEN_PATH_INDEX_MODE", "off") in {"shadow", "serve"}
        and repository.pulp_labels.get("path_index") == "true"
    )


def profile(domain):
    """Do not mix indexes after a domain/storage configuration change."""
    data = {
        "revision": 2,
        "domain": str(domain.pk),
        "storage": domain.storage_class,
        "options": domain.storage_settings,
        "default": settings.STORAGES if domain.name == "default" else None,
        "index": {
            "bucket": settings.MAVEN_PATH_INDEX_S3_BUCKET,
            "prefix": settings.MAVEN_PATH_INDEX_S3_PREFIX,
            "endpoint": settings.MAVEN_PATH_INDEX_S3_ENDPOINT,
        },
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def store(repository):
    """Instantiate after fork; credentials use boto3's normal credential chain."""
    if not settings.MAVEN_PATH_INDEX_S3_BUCKET:
        raise ValueError("MAVEN_PATH_INDEX_S3_BUCKET is required")

    import boto3
    from botocore.config import Config

    from .s3 import S3IndexStore

    return S3IndexStore(
        boto3.client(
            "s3",
            endpoint_url=settings.MAVEN_PATH_INDEX_S3_ENDPOINT,
            region_name=settings.MAVEN_PATH_INDEX_S3_REGION,
            config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}),
        ),
        settings.MAVEN_PATH_INDEX_S3_BUCKET,
        settings.MAVEN_PATH_INDEX_S3_PREFIX,
        settings.MAVEN_PATH_INDEX_CACHE_DIR,
        str(repository.pulp_domain_id),
        str(repository.pk),
        max_cache_bytes=settings.MAVEN_PATH_INDEX_CACHE_BYTES,
        work_directory=settings.MAVEN_PATH_INDEX_WORK_DIR,
    )
