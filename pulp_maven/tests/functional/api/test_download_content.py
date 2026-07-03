"""Tests that verify download of content served by Pulp."""

import hashlib
from urllib.parse import urljoin

from pulp_maven.tests.functional.utils import download_file


def test_download_content(
    maven_distribution_factory,
    maven_remote_factory,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_distro_api_client,
    maven_repo_api_client,
    monitor_task,
    distribution_base_url,
):
    """Verify whether content served by pulp can be downloaded.

    The process of creating a Maven mirror is:

    1. Create a Maven Remote with a URL pointing to the root of a Maven repository.
    2. Create a distribution with the remote set HREF from 1.

    Do the following:

    1. Create a Maven Remote and a Distribution.
    2. Select a random content unit in the distribution. Download that
       content unit from Pulp, and verify that the content unit has the
       same checksum when fetched directly from Maven Central.
    """
    remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")
    repository = maven_repo_factory(remote=remote.pulp_href)
    distribution = maven_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    # Pick a content unit, and download it from the remote repository
    unit_path = "academy/alex/custommatcher/1.0/custommatcher-1.0-javadoc.jar.sha1"
    remote_unit_url = urljoin(remote.url, unit_path)
    downloaded_file = download_file(remote_unit_url)
    remote_unit_checksum = hashlib.sha256(downloaded_file.body).hexdigest()

    # And from Pulp
    pulp_unit_url = urljoin(distribution_base_url(distribution.base_url), unit_path)
    downloaded_file = download_file(pulp_unit_url)
    pulp_unit_checksum = hashlib.sha256(downloaded_file.body).hexdigest()

    assert remote_unit_checksum == pulp_unit_checksum

    # Check that Pulp created a MavenArtifact
    content_response = maven_artifact_api_client.list(filename="custommatcher-1.0-javadoc.jar.sha1")
    assert content_response.count == 1

    # Pull-through cached content is automatically added to the repository.
    # The repository version should have incremented from 0 to 1.
    repository = maven_repo_api_client.read(repository.pulp_href)
    assert repository.latest_version_href.endswith("/versions/1/")

    # Verify the content is in the repository version
    repo_content = maven_artifact_api_client.list(repository_version=repository.latest_version_href)
    assert repo_content.count >= 1

    # Remove the remote from the distribution
    monitor_task(
        maven_distro_api_client.partial_update(distribution.pulp_href, {"remote": None}).task
    )

    # Content should still be available since it was automatically added to the repository
    downloaded_file = download_file(pulp_unit_url)
    assert downloaded_file.response_obj.status == 200


def test_pullthrough_idempotent(
    maven_distribution_factory,
    maven_remote_factory,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    distribution_base_url,
):
    """Verify that requesting the same content twice does not create duplicate repo versions."""
    remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")
    repository = maven_repo_factory(remote=remote.pulp_href)
    distribution = maven_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    unit_path = "academy/alex/custommatcher/1.0/custommatcher-1.0-javadoc.jar.sha1"
    pulp_unit_url = urljoin(distribution_base_url(distribution.base_url), unit_path)

    # First request — content is fetched from remote and added to the repository
    download_file(pulp_unit_url)
    repository = maven_repo_api_client.read(repository.pulp_href)
    first_version = repository.latest_version_href

    # Second request — content already exists; no new version should be created
    download_file(pulp_unit_url)
    repository = maven_repo_api_client.read(repository.pulp_href)
    assert repository.latest_version_href == first_version


def test_pullthrough_metadata_not_saved(
    maven_distribution_factory,
    maven_remote_factory,
    maven_repo_factory,
    maven_metadata_api_client,
    maven_repo_api_client,
    distribution_base_url,
):
    """Verify that maven-metadata.xml is streamed, not saved as content during pull-through."""
    remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")
    repository = maven_repo_factory(remote=remote.pulp_href)
    distribution = maven_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    metadata_path = "academy/alex/custommatcher/maven-metadata.xml"
    pulp_url = urljoin(distribution_base_url(distribution.base_url), metadata_path)

    downloaded = download_file(pulp_url)
    assert downloaded.response_obj.status == 200
    assert b"<artifactId>" in downloaded.body

    # No task is dispatched when get_remote_artifact_content_type returns None,
    # so there is nothing async to wait for.

    # maven-metadata.xml must NOT be saved as a MavenMetadata content unit
    metadata_content = maven_metadata_api_client.list(
        repository_version=maven_repo_api_client.read(repository.pulp_href).latest_version_href
    )
    for item in metadata_content.results:
        assert item.filename != "maven-metadata.xml", (
            "maven-metadata.xml should not be saved as content during pull-through"
        )

    # Repository version should not have been created for metadata
    repository = maven_repo_api_client.read(repository.pulp_href)
    assert repository.latest_version_href.endswith("/versions/0/")
