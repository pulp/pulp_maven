"""Tests for the repair_metadata repository action."""

import hashlib
import uuid
from urllib.parse import urljoin
from xml.etree import ElementTree

import pytest

from pulp_maven.tests.functional.utils import download_file


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture
def populated_repo(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Factory that creates a repo with artifacts, removes metadata, and returns context.

    Args:
        artifact_specs: list of (group_prefix, artifact_id, versions) tuples.
            e.g., ``[("com", "mylib", ["1.0.0", "2.0.0"])]``

    Returns:
        (repo, base_url, uid) after all metadata has been stripped so
        ``repair_metadata`` has to regenerate it from scratch.
    """

    def _factory(artifact_specs):
        repo = maven_repo_factory()
        distro = maven_distribution_factory(repository=repo.pulp_href)
        base_url = distribution_base_url(distro.base_url)
        # uid is embedded in the group path (e.g. "com/{uid}/mylib/...") so that
        # each test run produces unique group_ids and avoids collisions.
        uid = _uid()

        content_hrefs = []
        for group_prefix, artifact_id, versions in artifact_specs:
            for version in versions:
                artifact = random_artifact_factory(size=64)
                content = maven_artifact_api_client.upload(
                    artifact=artifact.pulp_href,
                    relative_path=(
                        f"{group_prefix}/{uid}/{artifact_id}/{version}/{artifact_id}-{version}.jar"
                    ),
                )
                content_hrefs.append(content.pulp_href)

        monitor_task(
            maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
        )

        # Remove auto-generated metadata so repair_metadata has to recreate it.
        repo = maven_repo_api_client.read(repo.pulp_href)
        metadata_hrefs = [
            m.pulp_href
            for m in maven_metadata_api_client.list(
                repository_version=repo.latest_version_href
            ).results
        ]
        if metadata_hrefs:
            monitor_task(
                maven_repo_api_client.modify(
                    repo.pulp_href, {"remove_content_units": metadata_hrefs}
                ).task
            )
        repo = maven_repo_api_client.read(repo.pulp_href)
        assert (
            maven_metadata_api_client.list(repository_version=repo.latest_version_href).count == 0
        )

        return repo, base_url, uid

    return _factory


@pytest.mark.parallel
def test_repair_metadata_restores_missing_metadata(
    populated_repo,
    maven_repo_api_client,
    monitor_task,
):
    """repair_metadata regenerates metadata that was removed from the repo."""
    repo, base_url, uid = populated_repo([("com", "repairlib", ["1.0.0", "2.0.0", "3.0.0"])])

    response = maven_repo_api_client.repair_metadata(repo.pulp_href)
    monitor_task(response.task)

    metadata_url = urljoin(base_url, f"com/{uid}/repairlib/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    assert downloaded.response_obj.status == 200

    root = ElementTree.fromstring(downloaded.body)
    assert root.findtext("groupId") == f"com.{uid}"
    assert root.findtext("artifactId") == "repairlib"

    versioning = root.find("versioning")
    assert versioning.findtext("latest") == "3.0.0"
    assert versioning.findtext("release") == "3.0.0"

    versions = sorted(v.text for v in versioning.findall("versions/version"))
    assert versions == ["1.0.0", "2.0.0", "3.0.0"]


@pytest.mark.parallel
def test_repair_metadata_fixes_stale_metadata(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
    tmp_path,
):
    """repair_metadata replaces stale metadata that only lists a subset of versions."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    content_hrefs = []
    for version in ["1.0.0", "2.0.0", "3.0.0"]:
        artifact = random_artifact_factory(size=64)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=f"com/{uid}/stalelib/{version}/stalelib-{version}.jar",
        )
        content_hrefs.append(content.pulp_href)

    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
    )

    stale_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<metadata><groupId>com.{uid}</groupId>"
        "<artifactId>stalelib</artifactId>"
        "<versioning><latest>1.0.0</latest><release>1.0.0</release>"
        "<versions><version>1.0.0</version></versions>"
        "<lastUpdated>20200101000000</lastUpdated></versioning></metadata>\n"
    )
    stale_file = tmp_path / "maven-metadata.xml"
    stale_file.write_text(stale_xml)

    stale_metadata = maven_metadata_api_client.upload(
        relative_path=f"com/{uid}/stalelib/maven-metadata.xml",
        file=str(stale_file),
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [stale_metadata.pulp_href]}
        ).task
    )

    metadata_url = urljoin(base_url, f"com/{uid}/stalelib/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    root = ElementTree.fromstring(downloaded.body)
    versions = [v.text for v in root.findall(".//versions/version")]
    assert versions == ["1.0.0"], "Metadata should be stale before repair"

    response = maven_repo_api_client.repair_metadata(repo.pulp_href)
    monitor_task(response.task)

    downloaded = download_file(metadata_url)
    root = ElementTree.fromstring(downloaded.body)
    versions = sorted(v.text for v in root.findall(".//versions/version"))
    assert versions == ["1.0.0", "2.0.0", "3.0.0"]
    assert root.find("versioning").findtext("latest") == "3.0.0"


@pytest.mark.parallel
def test_repair_metadata_creates_new_version(
    populated_repo,
    maven_metadata_api_client,
    maven_repo_api_client,
    monitor_task,
):
    """repair_metadata creates a new repository version with metadata content."""
    repo, _, _ = populated_repo([("com", "verlib", ["1.0.0"])])
    version_before = repo.latest_version_href

    response = maven_repo_api_client.repair_metadata(repo.pulp_href)
    monitor_task(response.task)

    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href != version_before

    metadata_list = maven_metadata_api_client.list(repository_version=repo.latest_version_href)
    assert metadata_list.count == 4  # xml + 3 checksums


@pytest.mark.parallel
def test_repair_metadata_checksums_match(
    populated_repo,
    maven_repo_api_client,
    monitor_task,
):
    """Checksum files match the repaired maven-metadata.xml content."""
    repo, base_url, uid = populated_repo([("com", "cksumlib", ["1.0.0"])])

    response = maven_repo_api_client.repair_metadata(repo.pulp_href)
    monitor_task(response.task)

    metadata_url = urljoin(base_url, f"com/{uid}/cksumlib/maven-metadata.xml")
    metadata_download = download_file(metadata_url)
    metadata_body = metadata_download.body

    for ext, hash_func in [
        (".md5", hashlib.md5),
        (".sha1", hashlib.sha1),
        (".sha256", hashlib.sha256),
    ]:
        checksum_url = urljoin(base_url, f"com/{uid}/cksumlib/maven-metadata.xml{ext}")
        checksum_download = download_file(checksum_url)
        assert checksum_download.response_obj.status == 200
        expected = hash_func(metadata_body).hexdigest()
        assert checksum_download.body.decode().strip() == expected, (
            f"Checksum mismatch for maven-metadata.xml{ext}"
        )


@pytest.mark.parallel
def test_repair_metadata_multiple_groups(
    populated_repo,
    maven_repo_api_client,
    monitor_task,
):
    """repair_metadata generates separate metadata for each (group_id, artifact_id) pair."""
    repo, base_url, uid = populated_repo([("com", "lib-x", ["1.0.0"]), ("org", "lib-y", ["2.0.0"])])

    response = maven_repo_api_client.repair_metadata(repo.pulp_href)
    monitor_task(response.task)

    dl_x = download_file(urljoin(base_url, f"com/{uid}/lib-x/maven-metadata.xml"))
    root_x = ElementTree.fromstring(dl_x.body)
    assert root_x.findtext("groupId") == f"com.{uid}"
    assert root_x.findtext("artifactId") == "lib-x"
    assert [v.text for v in root_x.findall(".//versions/version")] == ["1.0.0"]

    dl_y = download_file(urljoin(base_url, f"org/{uid}/lib-y/maven-metadata.xml"))
    root_y = ElementTree.fromstring(dl_y.body)
    assert root_y.findtext("groupId") == f"org.{uid}"
    assert root_y.findtext("artifactId") == "lib-y"
    assert [v.text for v in root_y.findall(".//versions/version")] == ["2.0.0"]


@pytest.mark.parallel
def test_repair_metadata_snapshot_handling(
    populated_repo,
    maven_repo_api_client,
    monitor_task,
):
    """repair_metadata sets <release> to the latest non-SNAPSHOT version."""
    repo, base_url, uid = populated_repo([("com", "snaplib", ["1.0.0", "2.0.0-SNAPSHOT"])])

    response = maven_repo_api_client.repair_metadata(repo.pulp_href)
    monitor_task(response.task)

    metadata_url = urljoin(base_url, f"com/{uid}/snaplib/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    root = ElementTree.fromstring(downloaded.body)

    versioning = root.find("versioning")
    assert versioning.findtext("latest") == "2.0.0-SNAPSHOT"
    assert versioning.findtext("release") == "1.0.0"

    versions = sorted(v.text for v in versioning.findall("versions/version"))
    assert versions == ["1.0.0", "2.0.0-SNAPSHOT"]


@pytest.mark.parallel
def test_repair_metadata_with_existing_metadata(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """repair_metadata succeeds when the repo already has auto-generated metadata.

    Regression test for https://github.com/pulp/pulp_maven/issues/395:
    finalize_new_version called _generate_metadata redundantly after
    repair_metadata had already placed metadata in the version, causing an
    AssertionError in _compute_counts.
    """
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    content_hrefs = []
    for version in ["1.0.0", "2.0.0"]:
        artifact = random_artifact_factory(size=64)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=f"com/{uid}/existlib/{version}/existlib-{version}.jar",
        )
        content_hrefs.append(content.pulp_href)

    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
    )

    # Verify metadata was auto-generated by finalize_new_version.
    repo = maven_repo_api_client.read(repo.pulp_href)
    meta_count = maven_metadata_api_client.list(repository_version=repo.latest_version_href).count
    assert meta_count > 0, "Expected auto-generated metadata before repair"

    # Call repair_metadata — this must not raise AssertionError.
    response = maven_repo_api_client.repair_metadata(repo.pulp_href)
    monitor_task(response.task)

    repo = maven_repo_api_client.read(repo.pulp_href)
    metadata_url = urljoin(base_url, f"com/{uid}/existlib/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    assert downloaded.response_obj.status == 200

    root = ElementTree.fromstring(downloaded.body)
    assert root.findtext("groupId") == f"com.{uid}"
    assert root.findtext("artifactId") == "existlib"

    versions = sorted(v.text for v in root.findall(".//versions/version"))
    assert versions == ["1.0.0", "2.0.0"]


@pytest.mark.parallel
def test_repair_metadata_empty_repo_noop(
    maven_repo_factory,
    maven_repo_api_client,
    monitor_task,
):
    """repair_metadata on an empty repo does not create a new version."""
    repo = maven_repo_factory()
    version_before = repo.latest_version_href

    response = maven_repo_api_client.repair_metadata(repo.pulp_href)
    monitor_task(response.task)

    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href == version_before
