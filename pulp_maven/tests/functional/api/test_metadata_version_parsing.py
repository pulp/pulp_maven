"""Tests for Maven metadata coordinate parsing during ingestion.

Regression tests for GH #503: artifact-level ``maven-metadata.xml`` files (and
their checksum siblings) whose artifactId contains a digit were mis-parsed — the
artifactId was treated as a version. That produced a bogus, non-null version, so
identical files ingested through different paths received different natural keys,
defeating deduplication and allowing multiple content units with the same
``relative_path`` in a single repository version (which later crashes the
path-index builder).
"""

import uuid

import pytest


@pytest.mark.parallel
def test_ingested_metadata_checksum_gets_null_version(
    maven_metadata_api_client,
    random_artifact_factory,
):
    """A maven-metadata.xml checksum for a digit-containing artifactId has no version.

    Uploading the ``.sha1`` of an artifact-level ``maven-metadata.xml`` while the
    parent ``maven-metadata.xml`` is absent must still yield ``version=None`` — the
    directory segment above the file is the artifactId (``pop3``), not a version.
    Before the fix, the digit in the artifactId caused it to be read as a version.
    """
    # artifactId contains a digit so the buggy digit-heuristic misreads it as a version.
    artifact_id = f"pop3{uuid.uuid4().hex[:8]}"
    relative_path = f"javax/mail/{artifact_id}/maven-metadata.xml.sha1"

    artifact = random_artifact_factory(size=64)
    content = maven_metadata_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
    )

    assert content.filename == "maven-metadata.xml.sha1"
    assert content.group_id == "javax.mail"
    assert content.artifact_id == artifact_id
    # Artifact-level metadata has no version; the buggy code returns the artifactId here.
    assert content.version is None


@pytest.mark.parallel
def test_ingested_version_level_metadata_keeps_snapshot_version(
    maven_metadata_api_client,
    random_artifact_factory,
):
    """A version-level (SNAPSHOT) maven-metadata.xml checksum keeps its version.

    Version-level ``maven-metadata.xml`` lives under a ``-SNAPSHOT`` directory, so
    that segment IS the version. Coordinate parsing must keep it (and not collapse
    it to None), even for the checksum sibling ingested before the parent xml.
    """
    artifact_id = f"pop3{uuid.uuid4().hex[:8]}"
    version = "1.0-SNAPSHOT"
    relative_path = f"javax/mail/{artifact_id}/{version}/maven-metadata.xml.sha1"

    artifact = random_artifact_factory(size=64)
    content = maven_metadata_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
    )

    assert content.filename == "maven-metadata.xml.sha1"
    assert content.group_id == "javax.mail"
    assert content.artifact_id == artifact_id
    assert content.version == version
