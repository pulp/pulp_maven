"""Tests for automatic metadata generation in finalize_new_version."""

import hashlib
import re
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
        assert (
            checksum_download.body.decode().strip() == expected
        ), f"Checksum mismatch for maven-metadata.xml{ext}"


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
    assert metadata_list.count == 5  # xml + 3 checksums + prefixes.txt

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
    bindings_cfg,
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
        # The deploy endpoint authorizes writes, so this has to authenticate.
        auth = aiohttp.BasicAuth(bindings_cfg.username, bindings_cfg.password)
        async with aiohttp.ClientSession(raise_for_status=True, auth=auth) as session:
            async with session.put(url, data=data, verify_ssl=False) as resp:
                return resp.status

    status = asyncio.run(_put(f"{deploy_prefix}/{jar_path}", jar_content))
    assert status == 201

    repo = maven_repo_api_client.read(repo.pulp_href)
    metadata_list = maven_metadata_api_client.list(repository_version=repo.latest_version_href)
    assert metadata_list.count == 5  # xml + 3 checksums + prefixes.txt

    metadata_url = urljoin(base_url, f"com/{uid}/deployed/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    assert downloaded.response_obj.status == 200

    root = ElementTree.fromstring(downloaded.body)
    assert root.findtext("groupId") == f"com.{uid}"
    assert root.findtext("artifactId") == "deployed"
    versions = [v.text for v in root.findall(".//versions/version")]
    assert versions == ["1.0.0"]


@pytest.mark.parallel
def test_version_level_metadata_generated_for_snapshot(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Adding SNAPSHOT artifacts generates version-level maven-metadata.xml."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/snaplib/1.0-SNAPSHOT/snaplib-1.0-SNAPSHOT.jar",
    )
    a2 = random_artifact_factory(size=64)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"com/{uid}/snaplib/1.0-SNAPSHOT/snaplib-1.0-SNAPSHOT.pom",
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"add_content_units": [c1.pulp_href, c2.pulp_href]},
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    # 4 repo-level + 4 version-level + prefixes.txt = 9
    metadata_list = maven_metadata_api_client.list(repository_version=repo.latest_version_href)
    assert metadata_list.count == 9

    # Check version-level metadata XML
    ver_url = urljoin(base_url, f"com/{uid}/snaplib/1.0-SNAPSHOT/maven-metadata.xml")
    downloaded = download_file(ver_url)
    assert downloaded.response_obj.status == 200

    root = ElementTree.fromstring(downloaded.body)
    assert root.findtext("groupId") == f"com.{uid}"
    assert root.findtext("artifactId") == "snaplib"
    assert root.findtext("version") == "1.0-SNAPSHOT"

    versioning = root.find("versioning")
    assert versioning.find("snapshot").findtext("localCopy") == "true"
    assert versioning.findtext("lastUpdated") is not None

    sv_list = versioning.findall("snapshotVersions/snapshotVersion")
    extensions = sorted(sv.findtext("extension") for sv in sv_list)
    assert extensions == ["jar", "pom"]
    for sv in sv_list:
        assert sv.findtext("value") == "1.0-SNAPSHOT"


@pytest.mark.parallel
def test_version_level_metadata_not_generated_for_release(
    maven_repo_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
):
    """Non-SNAPSHOT versions do not get version-level metadata."""
    repo = maven_repo_factory()
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/rellib/1.0.0/rellib-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    # Only repo-level metadata: xml + 3 checksums + prefixes.txt = 5
    metadata_list = maven_metadata_api_client.list(repository_version=repo.latest_version_href)
    assert metadata_list.count == 5

    # All metadata should have version=None (repo-level, plus prefixes.txt)
    for m in metadata_list.results:
        assert m.version is None


@pytest.mark.parallel
def test_version_level_metadata_checksums_match(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Version-level checksum files match the generated maven-metadata.xml."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/vcksum/1.0-SNAPSHOT/vcksum-1.0-SNAPSHOT.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    ver_url = urljoin(base_url, f"com/{uid}/vcksum/1.0-SNAPSHOT/maven-metadata.xml")
    metadata_download = download_file(ver_url)
    metadata_body = metadata_download.body

    for ext, hash_func in [
        (".md5", hashlib.md5),
        (".sha1", hashlib.sha1),
        (".sha256", hashlib.sha256),
    ]:
        checksum_url = urljoin(base_url, f"com/{uid}/vcksum/1.0-SNAPSHOT/maven-metadata.xml{ext}")
        checksum_download = download_file(checksum_url)
        assert checksum_download.response_obj.status == 200
        expected = hash_func(metadata_body).hexdigest()
        assert checksum_download.body.decode().strip() == expected


@pytest.mark.parallel
def test_version_level_metadata_removed_when_snapshot_removed(
    maven_repo_factory,
    maven_artifact_api_client,
    maven_metadata_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
):
    """Version-level metadata is removed when all SNAPSHOT artifacts are removed."""
    repo = maven_repo_factory()
    uid = _uid()

    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/rmsnap/1.0-SNAPSHOT/rmsnap-1.0-SNAPSHOT.jar",
    )
    a2 = random_artifact_factory(size=64)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"com/{uid}/rmsnap/2.0.0/rmsnap-2.0.0.jar",
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"add_content_units": [c1.pulp_href, c2.pulp_href]},
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    # 4 repo-level + 4 version-level + prefixes.txt = 9
    metadata_list = maven_metadata_api_client.list(repository_version=repo.latest_version_href)
    assert metadata_list.count == 9

    # Remove the SNAPSHOT artifact
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"remove_content_units": [c1.pulp_href]}).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    # Only repo-level metadata should remain (4 + prefixes.txt)
    metadata_list = maven_metadata_api_client.list(repository_version=repo.latest_version_href)
    assert metadata_list.count == 5
    for m in metadata_list.results:
        assert m.version is None


@pytest.mark.parallel
def test_version_level_metadata_with_classifier(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Version-level metadata includes classifier when present in the filename."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    content_hrefs = []
    for suffix, name in [
        ("jar", "clslib-1.0-SNAPSHOT.jar"),
        ("pom", "clslib-1.0-SNAPSHOT.pom"),
        ("jar", "clslib-1.0-SNAPSHOT-sources.jar"),
    ]:
        artifact = random_artifact_factory(size=64)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=f"com/{uid}/clslib/1.0-SNAPSHOT/{name}",
        )
        content_hrefs.append(content.pulp_href)

    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
    )

    ver_url = urljoin(base_url, f"com/{uid}/clslib/1.0-SNAPSHOT/maven-metadata.xml")
    downloaded = download_file(ver_url)
    root = ElementTree.fromstring(downloaded.body)

    sv_list = root.findall(".//snapshotVersions/snapshotVersion")
    assert len(sv_list) == 3

    entries = []
    for sv in sv_list:
        entry = {"extension": sv.findtext("extension")}
        classifier = sv.findtext("classifier")
        if classifier:
            entry["classifier"] = classifier
        entries.append(entry)

    entries.sort(key=lambda e: (e["extension"], e.get("classifier", "")))
    assert entries == [
        {"extension": "jar"},
        {"extension": "jar", "classifier": "sources"},
        {"extension": "pom"},
    ]


# --- Index page tests ---


@pytest.mark.parallel
def test_index_pages_generated_on_artifact_add(
    pulpcore_bindings,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
):
    """Adding artifacts auto-generates MavenIndexPage content at each ancestor directory."""
    repo = maven_repo_factory()
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/idx-lib/1.0.0/idx-lib-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    index_pages = pulpcore_bindings.ContentApi.list(
        repository_version=repo.latest_version_href,
        pulp_type__in=["maven.index-page"],
    )
    # Expect pages for: root, com/, com/{uid}/, com/{uid}/idx-lib/, com/{uid}/idx-lib/1.0.0/
    assert index_pages.count >= 5, f"Expected at least 5 index pages, got {index_pages.count}"


@pytest.mark.parallel
def test_index_page_html_downloadable_with_sizes_and_dates(
    pulpcore_bindings,
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Pre-generated directory index pages are served as HTML with file sizes and dates."""
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/html-lib/1.0.0/html-lib-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    # Discriminating assertion: index page content units must exist in the repo version.
    # Before the feature these are never generated; on-demand fallback produces no content units.
    index_pages = pulpcore_bindings.ContentApi.list(
        repository_version=repo.latest_version_href,
        pulp_type__in=["maven.index-page"],
    )
    assert (
        index_pages.count > 0
    ), "No maven.index-page content units found — pre-generation not implemented"

    # Verify the version-level directory is downloadable and contains expected entries.
    # The response must be 200 (inline HTML served by the content app), not a 302 redirect
    # to S3/Azure/GCS — content_handler reads the bytes and returns them directly so that
    # directory listings are never mis-served as attachments by object storage.
    dir_url = urljoin(base_url, f"com/{uid}/html-lib/1.0.0/")
    downloaded = download_file(dir_url)
    assert downloaded.response_obj.status == 200
    html = downloaded.body.decode()

    # Must be HTML — content_handler bypasses redirect-to-object-storage
    assert "<html" in html.lower(), "Response is not HTML — possible S3 redirect instead of inline"

    # Content-Disposition must NOT be 'attachment' (which object-storage redirects add)
    content_disposition = downloaded.response_obj.headers.get("Content-Disposition", "")
    assert (
        "attachment" not in content_disposition.lower()
    ), "Content-Disposition: attachment — index page was served as a download, not inline HTML"

    assert "html-lib-1.0.0.jar" in html, "Artifact filename missing from index page"
    # Size must appear (artifact was uploaded as 64 bytes)
    assert "64" in html, "File size missing from index page"
    # A year from the upload date must appear
    assert re.search(r"20\d{2}", html), "Date missing from index page"


@pytest.mark.parallel
def test_index_page_updated_on_new_artifact(
    pulpcore_bindings,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
):
    """Adding a second artifact at a new version regenerates the affected ancestor index pages."""
    repo = maven_repo_factory()
    uid = _uid()

    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/upd-lib/1.0.0/upd-lib-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [c1.pulp_href]}).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    pages_v1 = pulpcore_bindings.ContentApi.list(
        repository_version=repo.latest_version_href,
        pulp_type__in=["maven.index-page"],
    )
    assert pages_v1.count > 0
    v1_hrefs = {p.pulp_href for p in pages_v1.results}

    # Add a second version — shared ancestor directories must get new index pages
    a2 = random_artifact_factory(size=128)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"com/{uid}/upd-lib/2.0.0/upd-lib-2.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [c2.pulp_href]}).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    pages_v2 = pulpcore_bindings.ContentApi.list(
        repository_version=repo.latest_version_href,
        pulp_type__in=["maven.index-page"],
    )
    assert pages_v2.count > 0
    v2_hrefs = {p.pulp_href for p in pages_v2.results}

    # At least the com/{uid}/upd-lib/ index page must differ (it now lists two versions)
    assert v1_hrefs != v2_hrefs, (
        "Index page hrefs unchanged after adding a new artifact version — "
        "affected pages were not regenerated"
    )


@pytest.mark.parallel
def test_unaffected_directory_page_unchanged_after_sibling_add(
    pulpcore_bindings,
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Adding a second version directory regenerates ancestors but NOT sibling version dirs.

    After adding lib/1.0.0/lib.jar:
      index pages exist for: root, com/, com/{uid}/, com/{uid}/lib/, com/{uid}/lib/1.0.0/

    After adding lib/2.0.0/lib.jar:
      - com/{uid}/lib/ is regenerated (now lists both 1.0.0/ and 2.0.0/)
      - com/{uid}/lib/2.0.0/ is a brand-new page
      - com/{uid}/lib/1.0.0/ is UNCHANGED (same content, same href — no files added there)
    """
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/sel-lib/1.0.0/sel-lib-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [c1.pulp_href]}).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    # Capture v1 index page hrefs and HTML for key directories
    v1_pages = pulpcore_bindings.ContentApi.list(
        repository_version=repo.latest_version_href,
        pulp_type__in=["maven.index-page"],
        limit=100,
    )
    assert v1_pages.count >= 5
    v1_hrefs = {p.pulp_href for p in v1_pages.results}

    # Download directory listings while v1 is current
    v1_leaf_html = download_file(urljoin(base_url, f"com/{uid}/sel-lib/1.0.0/")).body
    v1_parent_html = download_file(urljoin(base_url, f"com/{uid}/sel-lib/")).body

    # Add 2.0.0 — touches: root, com/, com/{uid}/, com/{uid}/sel-lib/, com/{uid}/sel-lib/2.0.0/
    a2 = random_artifact_factory(size=128)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"com/{uid}/sel-lib/2.0.0/sel-lib-2.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [c2.pulp_href]}).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    v2_pages = pulpcore_bindings.ContentApi.list(
        repository_version=repo.latest_version_href,
        pulp_type__in=["maven.index-page"],
        limit=100,
    )
    v2_hrefs = {p.pulp_href for p in v2_pages.results}

    # 1.0.0/ was NOT in the affected paths → its index page content is identical →
    # the same MavenIndexPage content unit is reused (same href in both versions).
    unchanged = v1_hrefs & v2_hrefs
    assert len(unchanged) > 0, (
        "Expected at least one index page to be reused unchanged between v1 and v2 "
        "(e.g. sel-lib/1.0.0/ whose content didn't change)"
    )

    # sel-lib/ WAS in the affected paths and its listing changed → new href
    new_hrefs = v2_hrefs - v1_hrefs
    assert len(new_hrefs) > 0, "Expected sel-lib/ page to be regenerated with a new href"

    # Verify HTML: 1.0.0/ directory page is byte-for-byte identical in v1 and v2
    v2_leaf_html = download_file(urljoin(base_url, f"com/{uid}/sel-lib/1.0.0/")).body
    assert (
        v2_leaf_html == v1_leaf_html
    ), "com/{uid}/sel-lib/1.0.0/ HTML changed even though no files were added or removed there"

    # Verify HTML: sel-lib/ parent page was regenerated and now lists both versions
    v2_parent_html = download_file(urljoin(base_url, f"com/{uid}/sel-lib/")).body
    assert (
        v2_parent_html != v1_parent_html
    ), "com/{uid}/sel-lib/ HTML unchanged after adding 2.0.0/ — page was not regenerated"
    assert b"1.0.0/" in v2_parent_html, "1.0.0/ missing from regenerated sel-lib/ page"
    assert b"2.0.0/" in v2_parent_html, "2.0.0/ missing from regenerated sel-lib/ page"


@pytest.mark.parallel
def test_index_page_content_lists_only_direct_children(
    pulpcore_bindings,
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Each index page lists exactly its direct children — files in the directory and
    immediate subdirectory names — not files from deeper descendants.

    For lib/1.0.0/lib.jar and lib/1.0.0/lib.pom:
      - lib/          lists: 1.0.0/   (subdirectory only)
      - lib/1.0.0/    lists: lib.jar, lib.pom  (files only, no subdirectory names)
    """
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/acc-lib/1.0.0/acc-lib-1.0.0.jar",
    )
    a2 = random_artifact_factory(size=128)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"com/{uid}/acc-lib/1.0.0/acc-lib-1.0.0.pom",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [c1.pulp_href, c2.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    # Must have pre-generated index pages
    pages = pulpcore_bindings.ContentApi.list(
        repository_version=repo.latest_version_href,
        pulp_type__in=["maven.index-page"],
    )
    assert pages.count > 0

    # acc-lib/ lists 1.0.0/ (the subdirectory) but NOT individual files
    parent_html = download_file(urljoin(base_url, f"com/{uid}/acc-lib/")).body.decode()
    assert "1.0.0/" in parent_html, "1.0.0/ subdirectory missing from acc-lib/ page"
    assert (
        "acc-lib-1.0.0.jar" not in parent_html
    ), "acc-lib-1.0.0.jar appears in acc-lib/ page — only direct children should be listed"
    assert (
        "acc-lib-1.0.0.pom" not in parent_html
    ), "acc-lib-1.0.0.pom appears in acc-lib/ page — only direct children should be listed"

    # acc-lib/1.0.0/ lists the two files but NOT acc-lib/ as a navigable entry.
    # The parent is only accessible via the standard "../" link; the directory name
    # "acc-lib/" itself does appear in the page title ("Index of com/.../acc-lib/1.0.0/")
    # so we check for the entry link form, not for bare presence in the HTML.
    leaf_html = download_file(urljoin(base_url, f"com/{uid}/acc-lib/1.0.0/")).body.decode()
    assert "acc-lib-1.0.0.jar" in leaf_html, "jar missing from 1.0.0/ page"
    assert "acc-lib-1.0.0.pom" in leaf_html, "pom missing from 1.0.0/ page"
    assert 'href="./acc-lib/"' not in leaf_html, (
        "acc-lib/ appears as a navigable entry in the 1.0.0/ child page — "
        "only direct children should be linked"
    )


@pytest.mark.parallel
def test_index_page_regenerated_on_artifact_removal(
    pulpcore_bindings,
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Removing an artifact regenerates affected ancestor pages and drops the now-empty
    directory page, while sibling version pages are not touched.

    After adding lib/1.0.0/jar and lib/2.0.0/jar, then removing lib/2.0.0/jar:
      - lib/ is regenerated → only lists 1.0.0/  (not 2.0.0/ anymore)
      - lib/2.0.0/ index page disappears (directory is now empty)
      - lib/1.0.0/ page is unchanged (not affected by the removal)
    """
    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/rm-lib/1.0.0/rm-lib-1.0.0.jar",
    )
    a2 = random_artifact_factory(size=64)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"com/{uid}/rm-lib/2.0.0/rm-lib-2.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [c1.pulp_href, c2.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    # Capture baseline: both versions present
    pages_before = pulpcore_bindings.ContentApi.list(
        repository_version=repo.latest_version_href,
        pulp_type__in=["maven.index-page"],
        limit=100,
    )
    assert pages_before.count > 0
    hrefs_before = {p.pulp_href for p in pages_before.results}

    parent_html_before = download_file(urljoin(base_url, f"com/{uid}/rm-lib/")).body
    leaf_1_html_before = download_file(urljoin(base_url, f"com/{uid}/rm-lib/1.0.0/")).body
    assert b"1.0.0/" in parent_html_before
    assert b"2.0.0/" in parent_html_before

    # Remove 2.0.0 — affected paths: root, com/, com/{uid}/, com/{uid}/rm-lib/, com/{uid}/rm-lib/2.0.0/
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"remove_content_units": [c2.pulp_href]}).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    pages_after = pulpcore_bindings.ContentApi.list(
        repository_version=repo.latest_version_href,
        pulp_type__in=["maven.index-page"],
        limit=100,
    )
    assert pages_after.count > 0
    hrefs_after = {p.pulp_href for p in pages_after.results}

    # Some pages must have been regenerated (hrefs changed)
    assert hrefs_before != hrefs_after, "No index pages changed after removal"

    # The 2.0.0/ directory is now empty → its index page must be gone
    parent_html_after = download_file(urljoin(base_url, f"com/{uid}/rm-lib/")).body
    assert b"1.0.0/" in parent_html_after, "1.0.0/ missing from rm-lib/ page after removal"
    assert (
        b"2.0.0/" not in parent_html_after
    ), "2.0.0/ still appears in rm-lib/ page after its artifact was removed"

    # 1.0.0/ page is unchanged — the 2.0.0 removal didn't affect it
    leaf_1_html_after = download_file(urljoin(base_url, f"com/{uid}/rm-lib/1.0.0/")).body
    assert leaf_1_html_after == leaf_1_html_before, (
        "rm-lib/1.0.0/ HTML changed after removing 2.0.0/ — "
        "unaffected directory page was unexpectedly regenerated"
    )
