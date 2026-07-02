"""Tests for automatic metadata generation in finalize_new_version."""

import hashlib
import uuid
from urllib.parse import urljoin
from xml.etree import ElementTree

import pytest

from pulp_maven.tests.functional.utils import download_file


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.mark.parallel
def test_metadata_generated_on_artifact_add(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Adding artifacts auto-generates maven-metadata.xml with all versions listed."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    content_hrefs = []
    for version in ["1.0.0", "1.5.0", "2.0.0"]:
        artifact = random_artifact_factory(size=64)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=f"com/{uid}/mylib/{version}/mylib-{version}.jar",
        )
        content_hrefs.append(content.pulp_href)

    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    metadata_list = maven_metadata_api_client.list(repository_version=repo.latest_version_href)
    metadata_filenames = sorted(m.filename for m in metadata_list.results)
    assert "maven-metadata.xml" in metadata_filenames
    assert "maven-metadata.xml.md5" in metadata_filenames
    assert "maven-metadata.xml.sha1" in metadata_filenames
    assert "maven-metadata.xml.sha256" in metadata_filenames

    metadata_url = urljoin(base_url, f"com/{uid}/mylib/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    assert downloaded.response_obj.status == 200

    root = ElementTree.fromstring(downloaded.body)
    assert root.findtext("groupId") == f"com.{uid}"
    assert root.findtext("artifactId") == "mylib"

    versioning = root.find("versioning")
    assert versioning.findtext("latest") == "2.0.0"
    assert versioning.findtext("release") == "2.0.0"
    assert versioning.findtext("lastUpdated") is not None

    versions = sorted(v.text for v in versioning.findall("versions/version"))
    assert versions == ["1.0.0", "1.5.0", "2.0.0"]


@pytest.mark.parallel
def test_metadata_checksums_match_xml(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Checksum files (.md5, .sha1, .sha256) match the generated maven-metadata.xml."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/cksum-lib/1.0.0/cksum-lib-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    metadata_url = urljoin(base_url, f"com/{uid}/cksum-lib/maven-metadata.xml")
    metadata_download = download_file(metadata_url)
    metadata_body = metadata_download.body

    for ext, hash_func in [
        (".md5", hashlib.md5),
        (".sha1", hashlib.sha1),
        (".sha256", hashlib.sha256),
    ]:
        checksum_url = urljoin(base_url, f"com/{uid}/cksum-lib/maven-metadata.xml{ext}")
        checksum_download = download_file(checksum_url)
        assert checksum_download.response_obj.status == 200
        expected = hash_func(metadata_body).hexdigest()
        assert checksum_download.body.decode().strip() == expected, (
            f"Checksum mismatch for maven-metadata.xml{ext}"
        )


@pytest.mark.parallel
def test_metadata_release_excludes_snapshots(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """<release> is set to the latest non-SNAPSHOT version."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/snap-lib/1.0.0/snap-lib-1.0.0.jar",
    )
    a2 = random_artifact_factory(size=64)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"com/{uid}/snap-lib/2.0.0-SNAPSHOT/snap-lib-2.0.0-SNAPSHOT.jar",
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"add_content_units": [c1.pulp_href, c2.pulp_href]},
        ).task
    )

    metadata_url = urljoin(base_url, f"com/{uid}/snap-lib/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    root = ElementTree.fromstring(downloaded.body)

    versioning = root.find("versioning")
    assert versioning.findtext("latest") == "2.0.0-SNAPSHOT"
    assert versioning.findtext("release") == "1.0.0"

    versions = sorted(v.text for v in versioning.findall("versions/version"))
    assert versions == ["1.0.0", "2.0.0-SNAPSHOT"]


@pytest.mark.parallel
def test_metadata_multiple_groups(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Different (group_id, artifact_id) pairs get separate metadata files."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/lib-a/1.0.0/lib-a-1.0.0.jar",
    )
    a2 = random_artifact_factory(size=64)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"org/{uid}/lib-b/3.0.0/lib-b-3.0.0.jar",
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"add_content_units": [c1.pulp_href, c2.pulp_href]},
        ).task
    )

    metadata_url_a = urljoin(base_url, f"com/{uid}/lib-a/maven-metadata.xml")
    downloaded_a = download_file(metadata_url_a)
    root_a = ElementTree.fromstring(downloaded_a.body)
    assert root_a.findtext("groupId") == f"com.{uid}"
    assert root_a.findtext("artifactId") == "lib-a"
    versions_a = [v.text for v in root_a.findall(".//versions/version")]
    assert versions_a == ["1.0.0"]

    metadata_url_b = urljoin(base_url, f"org/{uid}/lib-b/maven-metadata.xml")
    downloaded_b = download_file(metadata_url_b)
    root_b = ElementTree.fromstring(downloaded_b.body)
    assert root_b.findtext("groupId") == f"org.{uid}"
    assert root_b.findtext("artifactId") == "lib-b"
    versions_b = [v.text for v in root_b.findall(".//versions/version")]
    assert versions_b == ["3.0.0"]


@pytest.mark.parallel
def test_metadata_updated_on_new_artifact(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Adding a new version in a subsequent repo version updates the metadata."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/evolve/1.0.0/evolve-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [c1.pulp_href]}).task
    )

    metadata_url = urljoin(base_url, f"com/{uid}/evolve/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    root = ElementTree.fromstring(downloaded.body)
    versions = [v.text for v in root.findall(".//versions/version")]
    assert versions == ["1.0.0"]

    a2 = random_artifact_factory(size=64)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"com/{uid}/evolve/2.0.0/evolve-2.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [c2.pulp_href]}).task
    )

    downloaded = download_file(metadata_url)
    root = ElementTree.fromstring(downloaded.body)
    versions = sorted(v.text for v in root.findall(".//versions/version"))
    assert versions == ["1.0.0", "2.0.0"]
    assert root.find("versioning").findtext("latest") == "2.0.0"


@pytest.mark.parallel
def test_metadata_updated_on_artifact_removal(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Removing a version updates the metadata to exclude it."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    content_hrefs = []
    for version in ["1.0.0", "2.0.0", "3.0.0"]:
        artifact = random_artifact_factory(size=64)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=f"com/{uid}/shrink/{version}/shrink-{version}.jar",
        )
        content_hrefs.append(content.pulp_href)

    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
    )

    metadata_url = urljoin(base_url, f"com/{uid}/shrink/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    root = ElementTree.fromstring(downloaded.body)
    versions = sorted(v.text for v in root.findall(".//versions/version"))
    assert versions == ["1.0.0", "2.0.0", "3.0.0"]

    # Remove version 2.0.0
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"remove_content_units": [content_hrefs[1]]}
        ).task
    )

    downloaded = download_file(metadata_url)
    root = ElementTree.fromstring(downloaded.body)
    versions = sorted(v.text for v in root.findall(".//versions/version"))
    assert versions == ["1.0.0", "3.0.0"]
    assert root.find("versioning").findtext("latest") == "3.0.0"


@pytest.mark.parallel
def test_metadata_removed_when_all_artifacts_removed(
    maven_repo_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
):
    """Metadata is removed when all artifact versions are removed from the repo."""
    repo = maven_repo_factory()
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/gone/1.0.0/gone-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    metadata_list = maven_metadata_api_client.list(repository_version=repo.latest_version_href)
    assert metadata_list.count == 4  # xml + 3 checksums

    # Remove the artifact
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"remove_content_units": [content.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    metadata_list = maven_metadata_api_client.list(repository_version=repo.latest_version_href)
    assert metadata_list.count == 0


@pytest.mark.parallel
def test_deploy_api_generates_metadata(
    maven_repo_factory,
    maven_distribution_factory,
    maven_metadata_api_client,
    maven_repo_api_client,
    distribution_base_url,
    pulp_settings,
):
    """Pushing an artifact via the deploy API auto-generates metadata."""
    import asyncio

    import aiohttp

    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    if pulp_settings.DOMAIN_ENABLED:
        deploy_prefix = f"http://localhost/pulp/maven/default/{repo.name}"
    else:
        deploy_prefix = f"http://localhost/pulp/maven/{repo.name}"

    jar_path = f"com/{uid}/deployed/1.0.0/deployed-1.0.0.jar"
    jar_content = b"fake jar content for metadata gen test"

    async def _put(url, data):
        async with aiohttp.ClientSession(raise_for_status=True) as session:
            async with session.put(url, data=data, verify_ssl=False) as resp:
                return resp.status

    status = asyncio.run(_put(f"{deploy_prefix}/{jar_path}", jar_content))
    assert status == 201

    repo = maven_repo_api_client.read(repo.pulp_href)
    metadata_list = maven_metadata_api_client.list(repository_version=repo.latest_version_href)
    assert metadata_list.count == 4  # xml + 3 checksums

    metadata_url = urljoin(base_url, f"com/{uid}/deployed/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    assert downloaded.response_obj.status == 200

    root = ElementTree.fromstring(downloaded.body)
    assert root.findtext("groupId") == f"com.{uid}"
    assert root.findtext("artifactId") == "deployed"
    versions = [v.text for v in root.findall(".//versions/version")]
    assert versions == ["1.0.0"]
