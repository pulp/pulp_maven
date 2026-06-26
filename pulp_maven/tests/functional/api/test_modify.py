"""Tests for the modify endpoint on Maven repositories."""

from urllib.parse import urljoin

import pytest

from pulp_maven.tests.functional.utils import download_file


@pytest.mark.parallel
def test_modify_add_content_units(
    maven_repo_factory,
    maven_remote_factory,
    maven_distribution_factory,
    maven_repo_api_client,
    maven_artifact_api_client,
    monitor_task,
    distribution_base_url,
):
    """Test adding content units from one Maven repo to another via modify."""
    remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")

    # Source repo: pull content through a distribution to populate the cache
    source_repo = maven_repo_factory(remote=remote.pulp_href)
    source_distro = maven_distribution_factory(
        remote=remote.pulp_href, repository=source_repo.pulp_href
    )

    unit_path = "academy/alex/custommatcher/1.0/custommatcher-1.0-javadoc.jar.sha1"
    pulp_unit_url = urljoin(distribution_base_url(source_distro.base_url), unit_path)
    downloaded_file = download_file(pulp_unit_url)
    assert downloaded_file.response_obj.status == 200

    # Content is automatically added to the repository via pull-through caching
    source_repo = maven_repo_api_client.read(source_repo.pulp_href)
    assert source_repo.latest_version_href.endswith("/versions/1/")

    # Find the content unit that was added
    content = maven_artifact_api_client.list(repository_version=source_repo.latest_version_href)
    assert content.count >= 1
    content_unit_href = content.results[0].pulp_href

    # Destination repo: empty
    dest_repo = maven_repo_factory()
    assert dest_repo.latest_version_href.endswith("/versions/0/")

    # Use modify to copy the content unit to the destination repo
    modify_response = maven_repo_api_client.modify(
        dest_repo.pulp_href, {"add_content_units": [content_unit_href]}
    )
    monitor_task(modify_response.task)

    dest_repo = maven_repo_api_client.read(dest_repo.pulp_href)
    assert dest_repo.latest_version_href.endswith("/versions/1/")

    # Verify the content is in the destination repo
    dest_content = maven_artifact_api_client.list(repository_version=dest_repo.latest_version_href)
    assert dest_content.count == 1
    assert dest_content.results[0].pulp_href == content_unit_href


@pytest.mark.parallel
def test_modify_remove_all_content_units(
    maven_repo_factory,
    maven_remote_factory,
    maven_distribution_factory,
    maven_repo_api_client,
    maven_artifact_api_client,
    monitor_task,
    distribution_base_url,
):
    """Test removing all content from a Maven repo via modify with '*'."""
    remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")

    repo = maven_repo_factory(remote=remote.pulp_href)
    distro = maven_distribution_factory(remote=remote.pulp_href, repository=repo.pulp_href)

    unit_path = "academy/alex/custommatcher/1.0/custommatcher-1.0-javadoc.jar.sha1"
    pulp_unit_url = urljoin(distribution_base_url(distro.base_url), unit_path)
    downloaded_file = download_file(pulp_unit_url)
    assert downloaded_file.response_obj.status == 200

    # Content is automatically added via pull-through caching
    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/1/")

    content = maven_artifact_api_client.list(repository_version=repo.latest_version_href)
    assert content.count >= 1

    # Remove all content using the wildcard
    modify_response = maven_repo_api_client.modify(repo.pulp_href, {"remove_content_units": ["*"]})
    monitor_task(modify_response.task)

    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/2/")

    # Verify repo is now empty
    dest_content = maven_artifact_api_client.list(repository_version=repo.latest_version_href)
    assert dest_content.count == 0


@pytest.mark.parallel
def test_modify_multiple_content_units(
    maven_repo_factory,
    maven_remote_factory,
    maven_distribution_factory,
    maven_repo_api_client,
    maven_artifact_api_client,
    monitor_task,
    distribution_base_url,
):
    """Test adding multiple content units at once via modify."""
    remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")

    source_repo = maven_repo_factory(remote=remote.pulp_href)
    source_distro = maven_distribution_factory(
        remote=remote.pulp_href, repository=source_repo.pulp_href
    )

    # Pull two different content units through the cache
    unit_paths = [
        "academy/alex/custommatcher/1.0/custommatcher-1.0-javadoc.jar.sha1",
        "academy/alex/custommatcher/1.0/custommatcher-1.0.pom.sha1",
    ]
    for unit_path in unit_paths:
        pulp_unit_url = urljoin(distribution_base_url(source_distro.base_url), unit_path)
        downloaded_file = download_file(pulp_unit_url)
        assert downloaded_file.response_obj.status == 200

    # Content is automatically added via pull-through caching
    source_repo = maven_repo_api_client.read(source_repo.pulp_href)

    content = maven_artifact_api_client.list(repository_version=source_repo.latest_version_href)
    assert content.count >= 2
    content_hrefs = [c.pulp_href for c in content.results[:2]]

    # Copy multiple units to destination
    dest_repo = maven_repo_factory()
    modify_response = maven_repo_api_client.modify(
        dest_repo.pulp_href, {"add_content_units": content_hrefs}
    )
    monitor_task(modify_response.task)

    dest_repo = maven_repo_api_client.read(dest_repo.pulp_href)
    assert dest_repo.latest_version_href.endswith("/versions/1/")

    dest_content = maven_artifact_api_client.list(repository_version=dest_repo.latest_version_href)
    assert dest_content.count == len(content_hrefs)
