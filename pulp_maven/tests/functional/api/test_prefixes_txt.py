"""Tests for .meta/prefixes.txt generation"""

import uuid
from urllib.parse import urljoin

import pytest

from pulp_maven.tests.functional.utils import download_file

PREFIXES_TXT_FILENAME = ".meta/prefixes.txt"


def _uid():
    # Each test generates unique group IDs using this random prefix, so
    # parallel test runs don't interfere with each other. Without this,
    # two tests both uploading "com/example/..." would see each other's
    # prefixes.txt entries.
    return uuid.uuid4().hex[:8]


def _parse_prefixes(body):
    """Parse prefixes.txt body bytes into (header, sorted prefix list)."""
    text = body.decode("utf-8")
    lines = [line for line in text.strip().split("\n") if line]
    header = lines[0] if lines else ""
    prefixes = sorted(lines[1:]) if len(lines) > 1 else []
    return header, prefixes


@pytest.mark.parallel
def test_prefixes_txt_generates_on_artifact_add(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Adding artifacts generates .meta/prefixes.txt with correct prefixes."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    content_hrefs = []
    # Two different prefixes: /com/{uid} and /org/{uid}
    for group_prefix, artifact_id, version in [
        (f"com/{uid}/sub", "lib-a", "1.0.0"),
        (f"org/{uid}/sub", "lib-b", "2.0.0"),
    ]:
        artifact = random_artifact_factory(size=64)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=f"{group_prefix}/{artifact_id}/{version}/{artifact_id}-{version}.jar",
        )
        content_hrefs.append(content.pulp_href)
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
    )

    prefixes_url = urljoin(base_url, PREFIXES_TXT_FILENAME)
    downloaded = download_file(prefixes_url)
    assert downloaded.response_obj.status == 200

    header, prefixes = _parse_prefixes(downloaded.body)
    assert header == "## repository-prefixes/2.0"
    assert f"/com/{uid}" in prefixes
    assert f"/org/{uid}" in prefixes
    assert len(prefixes) == 2


@pytest.mark.parallel
def test_prefixes_txt_updated_on_new_prefix(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Adding an artifact with a new prefix updates prefixes.txt"""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    # Add first artifact, creates initial prefixes.txt
    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/1.0/pulp/1.0/pulp-1.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [c1.pulp_href]}).task
    )

    prefixes_url = urljoin(base_url, PREFIXES_TXT_FILENAME)
    downloaded = download_file(prefixes_url)
    header, prefixes = _parse_prefixes(downloaded.body)
    assert header == "## repository-prefixes/2.0"
    assert prefixes == [f"/com/{uid}"]

    # Add artifact with NEW prefix
    a2 = random_artifact_factory(size=64)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"org/{uid}/1.0/other-pulp/1.0/other-pulp-1.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [c2.pulp_href]}).task
    )

    downloaded = download_file(prefixes_url)
    _, prefixes_after = _parse_prefixes(downloaded.body)
    assert sorted(prefixes_after) == sorted([f"/com/{uid}", f"/org/{uid}"])


@pytest.mark.parallel
def test_prefixes_txt_updated_on_last_prefix_removed(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Removing the last artifact with a prefix updates prefixes.txt."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    content_hrefs = []
    # Two different prefixes: /com/{uid} and /org/{uid}
    for group_prefix, artifact_id, version in [
        (f"com/{uid}/sub", "lib-a", "1.0.0"),
        (f"com/{uid}/sub", "lib-c", "2.0.0"),
        (f"org/{uid}/sub", "lib-b", "2.0.0"),
    ]:
        artifact = random_artifact_factory(size=64)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=f"{group_prefix}/{artifact_id}/{version}/{artifact_id}-{version}.jar",
        )
        content_hrefs.append(content.pulp_href)
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
    )

    prefixes_url = urljoin(base_url, PREFIXES_TXT_FILENAME)
    downloaded = download_file(prefixes_url)
    assert downloaded.response_obj.status == 200

    header, prefixes = _parse_prefixes(downloaded.body)
    assert header == "## repository-prefixes/2.0"
    assert sorted(prefixes) == [f"/com/{uid}", f"/org/{uid}"]

    removing = content_hrefs[2]
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"remove_content_units": [removing]}).task
    )

    downloaded = download_file(prefixes_url)
    _, prefixes_after = _parse_prefixes(downloaded.body)
    assert prefixes_after == [f"/com/{uid}"]


@pytest.mark.parallel
def test_prefixes_txt_not_regenerated_within_same_prefix(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Adding an artifact within an existing prefix does NOT regenerate prefixes.txt."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    # Add first artifact, creates initial prefixes.txt
    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/1.0/pulp/1.0/pulp-1.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [c1.pulp_href]}).task
    )

    prefixes_url = urljoin(base_url, PREFIXES_TXT_FILENAME)
    downloaded = download_file(prefixes_url)
    assert downloaded.response_obj.status == 200

    header, prefixes = _parse_prefixes(downloaded.body)
    assert header == "## repository-prefixes/2.0"
    assert prefixes == [f"/com/{uid}"]

    # Add new artifact with the SAME prefix
    a2 = random_artifact_factory(size=64)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"com/{uid}/2.0/test-pulp/2.0/test-pulp-1.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [c2.pulp_href]}).task
    )

    downloaded = download_file(prefixes_url)
    assert downloaded.response_obj.status == 200

    header, prefixes = _parse_prefixes(downloaded.body)
    assert header == "## repository-prefixes/2.0"
    assert prefixes == [f"/com/{uid}"]


@pytest.mark.parallel
def test_prefixes_txt_downloadable_from_content_app(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """The .meta/prefixes.txt file is downloadable from the content app."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/dltest/1.0.0/dltest-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    prefixes_url = urljoin(base_url, ".meta/prefixes.txt")
    downloaded = download_file(prefixes_url)
    assert downloaded.response_obj.status == 200

    text = downloaded.body.decode("utf-8")
    assert text.startswith("## repository-prefixes/2.0\n")
    assert f"/com/{uid}" in text


@pytest.mark.parallel
def test_prefixes_txt_removed_when_all_artifacts_removed(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Removing all artifacts from a repository also removes prefixes.txt."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    content_hrefs = []
    for group_prefix, artifact_id, version in [
        (f"com/{uid}/sub", "lib-a", "1.0.0"),
        (f"org/{uid}/sub", "lib-b", "2.0.0"),
    ]:
        artifact = random_artifact_factory(size=64)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=f"{group_prefix}/{artifact_id}/{version}/{artifact_id}-{version}.jar",
        )
        content_hrefs.append(content.pulp_href)
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
    )

    prefixes_url = urljoin(base_url, PREFIXES_TXT_FILENAME)
    downloaded = download_file(prefixes_url)
    assert downloaded.response_obj.status == 200

    header, prefixes = _parse_prefixes(downloaded.body)
    assert header == "## repository-prefixes/2.0"
    assert len(prefixes) == 2

    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"remove_content_units": content_hrefs}).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    metadata = maven_metadata_api_client.list(
        repository_version=repo.latest_version_href,
        filename=PREFIXES_TXT_FILENAME,
    )
    assert metadata.count == 0


@pytest.mark.parallel
def test_prefixes_txt_generated_for_existing_repo_without_prefixes(
    maven_repo_factory,
    maven_remote_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Repos populated via pull-through (no prefixes.txt) get one on next modify.

    Pull-through caching bypasses _generate_metadata, so repositories that were
    populated before the prefixes.txt feature have artifacts but no
    prefixes.txt.  When a new artifact is added under an already-existing
    prefix, prefix_set_changed is False.  The fix detects the missing file and
    generates it anyway.
    """
    remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")
    repo = maven_repo_factory(remote=remote.pulp_href)
    distro = maven_distribution_factory(remote=remote.pulp_href, repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)

    # Pull an artifact through the cache — this adds MavenArtifact content
    # without running _generate_metadata, simulating a pre-existing repo.
    pull_through_path = "academy/alex/custommatcher/1.0/custommatcher-1.0-javadoc.jar.sha1"
    downloaded = download_file(urljoin(base_url, pull_through_path))
    assert downloaded.response_obj.status == 200

    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/1/")

    # Confirm no prefixes.txt exists yet
    metadata = maven_metadata_api_client.list(
        repository_version=repo.latest_version_href,
        filename=PREFIXES_TXT_FILENAME,
    )
    assert metadata.count == 0

    # Add another artifact under the SAME prefix ("academy") via modify.
    # Before the fix, prefix_set_changed was False and no prefixes.txt
    # was generated because the old code never checked for file existence.
    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path="academy/alex/other-lib/1.0.0/other-lib-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    # prefixes.txt should now exist with the /academy/alex prefix
    prefixes_url = urljoin(base_url, PREFIXES_TXT_FILENAME)
    downloaded = download_file(prefixes_url)
    assert downloaded.response_obj.status == 200

    header, prefixes = _parse_prefixes(downloaded.body)
    assert header == "## repository-prefixes/2.0"
    assert "/academy/alex" in prefixes
