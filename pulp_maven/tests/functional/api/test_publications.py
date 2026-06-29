"""Tests for Maven publications."""

import asyncio
import hashlib
import uuid
from urllib.parse import urljoin
from xml.etree import ElementTree

import aiohttp
import pytest

from pulpcore.client.pulp_maven import ApiException

from pulp_maven.tests.functional.utils import download_file


def _put_file(url, data):
    """Upload bytes to a URL via HTTP PUT (Maven deploy API)."""

    async def _do_put():
        async with aiohttp.ClientSession(raise_for_status=True) as session:
            async with session.put(url, data=data, verify_ssl=False) as resp:
                return resp.status

    return asyncio.run(_do_put())


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.mark.parallel
def test_create_publication_with_repository(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    gen_object_with_cleanup,
    monitor_task,
):
    """Test creating a publication using a repository (uses latest version)."""
    repo = maven_repo_factory()
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/pub-test/1.0.0/pub-test-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    publication = gen_object_with_cleanup(
        maven_publication_api_client, {"repository": repo.pulp_href}
    )
    assert publication.repository_version == repo.latest_version_href


@pytest.mark.parallel
def test_create_publication_with_repository_version(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    gen_object_with_cleanup,
    monitor_task,
):
    """Test creating a publication using a specific repository version."""
    repo = maven_repo_factory()
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/ver-test/1.0.0/ver-test-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)
    first_version_href = repo.latest_version_href

    artifact2 = random_artifact_factory(size=64)
    content2 = maven_artifact_api_client.upload(
        artifact=artifact2.pulp_href,
        relative_path=f"com/{uid}/ver-test/2.0.0/ver-test-2.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content2.pulp_href]}
        ).task
    )

    publication = gen_object_with_cleanup(
        maven_publication_api_client,
        {"repository_version": first_version_href},
    )
    assert publication.repository_version == first_version_href


@pytest.mark.parallel
def test_create_publication_errors(
    maven_publication_api_client,
    maven_repo_factory,
    gen_object_with_cleanup,
):
    """Test that specifying both repository and repository_version returns 400."""
    repo = maven_repo_factory()

    with pytest.raises(ApiException) as exc_info:
        gen_object_with_cleanup(
            maven_publication_api_client,
            {
                "repository": repo.pulp_href,
                "repository_version": repo.latest_version_href,
            },
        )
    assert exc_info.value.status == 400


@pytest.mark.parallel
def test_list_and_delete_publication(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    gen_object_with_cleanup,
    monitor_task,
):
    """Test listing and deleting publications."""
    repo = maven_repo_factory()
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/crud/1.0.0/crud-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    publication = gen_object_with_cleanup(
        maven_publication_api_client,
        {"repository_version": repo.latest_version_href},
    )

    results = maven_publication_api_client.list(repository=repo.pulp_href)
    assert results.count == 1
    assert results.results[0].pulp_href == publication.pulp_href

    results = maven_publication_api_client.list(repository_version=repo.latest_version_href)
    assert results.count == 1

    pub = maven_publication_api_client.read(publication.pulp_href)
    assert pub.pulp_href == publication.pulp_href

    maven_publication_api_client.delete(publication.pulp_href)
    with pytest.raises(ApiException) as exc_info:
        maven_publication_api_client.read(publication.pulp_href)
    assert exc_info.value.status == 404


@pytest.mark.parallel
def test_publish_empty_repository(
    maven_publication_api_client,
    maven_repo_factory,
    gen_object_with_cleanup,
):
    """Test that publishing an empty repository version succeeds without error."""
    repo = maven_repo_factory()

    publication = gen_object_with_cleanup(
        maven_publication_api_client,
        {"repository_version": repo.latest_version_href},
    )
    assert publication.repository_version == repo.latest_version_href


@pytest.mark.parallel
def test_publication_generates_maven_metadata_xml(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_distribution_factory,
    random_artifact_factory,
    gen_object_with_cleanup,
    monitor_task,
    distribution_base_url,
):
    """Test that publishing generates maven-metadata.xml listing all versions."""
    repo = maven_repo_factory()
    uid = _uid()

    artifact_v1 = random_artifact_factory(size=64)
    content_v1 = maven_artifact_api_client.upload(
        artifact=artifact_v1.pulp_href,
        relative_path=f"com/{uid}/multi-ver/1.0.0/multi-ver-1.0.0.jar",
    )
    artifact_v2 = random_artifact_factory(size=64)
    content_v2 = maven_artifact_api_client.upload(
        artifact=artifact_v2.pulp_href,
        relative_path=f"com/{uid}/multi-ver/2.0.0/multi-ver-2.0.0.jar",
    )
    artifact_v3 = random_artifact_factory(size=64)
    content_v3 = maven_artifact_api_client.upload(
        artifact=artifact_v3.pulp_href,
        relative_path=f"com/{uid}/multi-ver/1.5.0/multi-ver-1.5.0.jar",
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {
                "add_content_units": [
                    content_v1.pulp_href,
                    content_v2.pulp_href,
                    content_v3.pulp_href,
                ]
            },
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    publication = gen_object_with_cleanup(
        maven_publication_api_client,
        {"repository_version": repo.latest_version_href},
    )
    distribution = maven_distribution_factory(publication=publication.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    metadata_url = urljoin(base_url, f"com/{uid}/multi-ver/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    assert downloaded.response_obj.status == 200

    root = ElementTree.fromstring(downloaded.body)
    assert root.findtext("groupId") == f"com.{uid}"
    assert root.findtext("artifactId") == "multi-ver"

    versioning = root.find("versioning")
    assert versioning is not None
    assert versioning.findtext("latest") == "2.0.0"
    assert versioning.findtext("lastUpdated") is not None

    versions = sorted(v.text for v in versioning.findall("versions/version"))
    assert versions == ["1.0.0", "1.5.0", "2.0.0"]


@pytest.mark.parallel
def test_publication_metadata_release_excludes_snapshots(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_distribution_factory,
    random_artifact_factory,
    gen_object_with_cleanup,
    monitor_task,
    distribution_base_url,
):
    """Test that <release> is set to the latest non-SNAPSHOT version."""
    repo = maven_repo_factory()
    uid = _uid()

    a1 = random_artifact_factory(size=64)
    c1 = maven_artifact_api_client.upload(
        artifact=a1.pulp_href,
        relative_path=f"com/{uid}/snap-test/1.0.0/snap-test-1.0.0.jar",
    )
    a2 = random_artifact_factory(size=64)
    c2 = maven_artifact_api_client.upload(
        artifact=a2.pulp_href,
        relative_path=f"com/{uid}/snap-test/2.0.0-SNAPSHOT/snap-test-2.0.0-SNAPSHOT.jar",
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"add_content_units": [c1.pulp_href, c2.pulp_href]},
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    publication = gen_object_with_cleanup(
        maven_publication_api_client,
        {"repository_version": repo.latest_version_href},
    )
    distribution = maven_distribution_factory(publication=publication.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    metadata_url = urljoin(base_url, f"com/{uid}/snap-test/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    root = ElementTree.fromstring(downloaded.body)

    versioning = root.find("versioning")
    assert versioning.findtext("latest") == "2.0.0-SNAPSHOT"
    assert versioning.findtext("release") == "1.0.0"

    versions = sorted(v.text for v in versioning.findall("versions/version"))
    assert versions == ["1.0.0", "2.0.0-SNAPSHOT"]


@pytest.mark.parallel
def test_publication_generates_metadata_checksum_files(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_distribution_factory,
    random_artifact_factory,
    gen_object_with_cleanup,
    monitor_task,
    distribution_base_url,
):
    """Test that checksums are generated for the generated maven-metadata.xml."""
    repo = maven_repo_factory()
    uid = _uid()

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/meta-cksum/1.0.0/meta-cksum-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    publication = gen_object_with_cleanup(
        maven_publication_api_client,
        {"repository_version": repo.latest_version_href},
    )
    distribution = maven_distribution_factory(publication=publication.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    metadata_url = urljoin(base_url, f"com/{uid}/meta-cksum/maven-metadata.xml")
    metadata_download = download_file(metadata_url)
    metadata_body = metadata_download.body

    for ext, hash_func in [
        (".md5", hashlib.md5),
        (".sha1", hashlib.sha1),
        (".sha256", hashlib.sha256),
    ]:
        checksum_url = urljoin(base_url, f"com/{uid}/meta-cksum/maven-metadata.xml{ext}")
        checksum_download = download_file(checksum_url)
        assert checksum_download.response_obj.status == 200
        expected = hash_func(metadata_body).hexdigest()
        assert checksum_download.body.decode().strip() == expected, (
            f"Checksum mismatch for maven-metadata.xml{ext}"
        )


@pytest.mark.parallel
def test_publication_multiple_artifact_groups(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_distribution_factory,
    random_artifact_factory,
    gen_object_with_cleanup,
    monitor_task,
    distribution_base_url,
):
    """Test that separate maven-metadata.xml files are generated for different groups."""
    repo = maven_repo_factory()
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
    repo = maven_repo_api_client.read(repo.pulp_href)

    publication = gen_object_with_cleanup(
        maven_publication_api_client,
        {"repository_version": repo.latest_version_href},
    )
    distribution = maven_distribution_factory(publication=publication.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

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

    for path in [
        f"com/{uid}/lib-a/maven-metadata.xml.sha256",
        f"org/{uid}/lib-b/maven-metadata.xml.sha256",
    ]:
        url = urljoin(base_url, path)
        dl = download_file(url)
        assert dl.response_obj.status == 200
        assert len(dl.body.decode().strip()) == 64


@pytest.mark.parallel
def test_publication_pass_through_serves_original_content(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_distribution_factory,
    random_artifact_factory,
    gen_object_with_cleanup,
    monitor_task,
    distribution_base_url,
):
    """Test that original artifacts are still served through a pass-through publication."""
    repo = maven_repo_factory()
    uid = _uid()

    artifact = random_artifact_factory(size=256)
    relative_path = f"com/{uid}/passthrough/1.0.0/passthrough-1.0.0.jar"
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    publication = gen_object_with_cleanup(
        maven_publication_api_client,
        {"repository_version": repo.latest_version_href},
    )
    distribution = maven_distribution_factory(publication=publication.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    artifact_url = urljoin(base_url, relative_path)
    downloaded = download_file(artifact_url)
    assert downloaded.response_obj.status == 200
    assert hashlib.sha256(downloaded.body).hexdigest() == artifact.sha256


@pytest.mark.parallel
def test_distribution_serves_content_from_publication(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_distro_api_client,
    maven_distribution_factory,
    random_artifact_factory,
    gen_object_with_cleanup,
    monitor_task,
    distribution_base_url,
):
    """Test that adding a publication to a distribution makes its content downloadable."""
    repo = maven_repo_factory()
    uid = _uid()

    # Upload two packages, each with multiple versions
    content_hrefs = []
    artifacts = {}
    for art_id, version, size in [
        ("app-core", "1.0.0", 64),
        ("app-core", "1.1.0", 96),
        ("app-core", "2.0.0", 128),
        ("app-web", "3.0.0", 80),
        ("app-web", "3.1.0-SNAPSHOT", 112),
    ]:
        artifact = random_artifact_factory(size=size)
        jar = f"{art_id}-{version}.jar"
        rel_path = f"com/{uid}/{art_id}/{version}/{jar}"
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=rel_path,
        )
        content_hrefs.append(content.pulp_href)
        artifacts[rel_path] = artifact.sha256

    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)

    publication = gen_object_with_cleanup(
        maven_publication_api_client,
        {"repository_version": repo.latest_version_href},
    )

    # Create a distribution with no publication and no repository
    distribution = maven_distribution_factory()
    base_url = distribution_base_url(distribution.base_url)

    # Content should not be available yet
    with pytest.raises(Exception):
        download_file(urljoin(base_url, f"com/{uid}/app-core/1.0.0/app-core-1.0.0.jar"))

    # Add the publication to the distribution
    monitor_task(
        maven_distro_api_client.partial_update(
            distribution.pulp_href, {"publication": publication.pulp_href}
        ).task
    )

    # All original artifacts should be downloadable (pass-through)
    for rel_path, expected_sha256 in artifacts.items():
        downloaded = download_file(urljoin(base_url, rel_path))
        assert downloaded.response_obj.status == 200
        assert hashlib.sha256(downloaded.body).hexdigest() == expected_sha256

    # Verify maven-metadata.xml for app-core (3 versions)
    metadata_url = urljoin(base_url, f"com/{uid}/app-core/maven-metadata.xml")
    metadata_dl = download_file(metadata_url)
    assert metadata_dl.response_obj.status == 200
    root = ElementTree.fromstring(metadata_dl.body)
    assert root.findtext("groupId") == f"com.{uid}"
    assert root.findtext("artifactId") == "app-core"
    versions = sorted(v.text for v in root.findall(".//versions/version"))
    assert versions == ["1.0.0", "1.1.0", "2.0.0"]
    assert root.find("versioning").findtext("latest") == "2.0.0"
    assert root.find("versioning").findtext("release") == "2.0.0"

    # Verify maven-metadata.xml for app-web (2 versions, one SNAPSHOT)
    metadata_url = urljoin(base_url, f"com/{uid}/app-web/maven-metadata.xml")
    metadata_dl = download_file(metadata_url)
    assert metadata_dl.response_obj.status == 200
    root = ElementTree.fromstring(metadata_dl.body)
    assert root.findtext("groupId") == f"com.{uid}"
    assert root.findtext("artifactId") == "app-web"
    versions = sorted(v.text for v in root.findall(".//versions/version"))
    assert versions == ["3.0.0", "3.1.0-SNAPSHOT"]
    assert root.find("versioning").findtext("latest") == "3.1.0-SNAPSHOT"
    assert root.find("versioning").findtext("release") == "3.0.0"


@pytest.mark.parallel
def test_autopublish_creates_publication(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
):
    """Test that autopublish creates a publication when a new repo version is created."""
    repo = maven_repo_factory(autopublish=True)
    uid = _uid()

    pubs = maven_publication_api_client.list(repository=repo.pulp_href)
    assert pubs.count == 0

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/auto-pub/1.0.0/auto-pub-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    pubs = maven_publication_api_client.list(repository=repo.pulp_href)
    assert pubs.count == 1

    repo = maven_repo_api_client.read(repo.pulp_href)
    pub = pubs.results[0]
    assert pub.repository_version == repo.latest_version_href


@pytest.mark.parallel
def test_autopublish_successive_versions(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_distribution_factory,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Test that autopublish creates a new publication for each repository version."""
    repo = maven_repo_factory(autopublish=True)
    uid = _uid()

    artifact1 = random_artifact_factory(size=64)
    content1 = maven_artifact_api_client.upload(
        artifact=artifact1.pulp_href,
        relative_path=f"com/{uid}/auto-multi/1.0.0/auto-multi-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content1.pulp_href]}
        ).task
    )

    pubs = maven_publication_api_client.list(repository=repo.pulp_href)
    assert pubs.count == 1

    artifact2 = random_artifact_factory(size=64)
    content2 = maven_artifact_api_client.upload(
        artifact=artifact2.pulp_href,
        relative_path=f"com/{uid}/auto-multi/2.0.0/auto-multi-2.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content2.pulp_href]}
        ).task
    )

    pubs = maven_publication_api_client.list(repository=repo.pulp_href)
    assert pubs.count == 2

    repo = maven_repo_api_client.read(repo.pulp_href)
    latest_pub = maven_publication_api_client.list(
        repository_version=repo.latest_version_href
    ).results[0]
    distribution = maven_distribution_factory(publication=latest_pub.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    metadata_url = urljoin(base_url, f"com/{uid}/auto-multi/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    assert downloaded.response_obj.status == 200

    root = ElementTree.fromstring(downloaded.body)
    versions = sorted(v.text for v in root.findall(".//versions/version"))
    assert versions == ["1.0.0", "2.0.0"]


@pytest.mark.parallel
def test_autopublish_on_deploy_api_metadata_upload(
    maven_publication_api_client,
    maven_repo_factory,
    maven_repo_api_client,
    maven_distribution_factory,
    distribution_base_url,
    pulp_settings,
):
    """Test that pushing maven-metadata.xml via the deploy API triggers autopublish."""
    repo = maven_repo_factory(autopublish=True)
    maven_distribution_factory(repository=repo.pulp_href)
    uid = _uid()

    if pulp_settings.DOMAIN_ENABLED:
        deploy_prefix = f"http://localhost/pulp/maven/default/{repo.name}"
    else:
        deploy_prefix = f"http://localhost/pulp/maven/{repo.name}"

    # Push a jar first (creates repo version 1)
    jar_path = f"com/{uid}/deploy-meta/1.0.0/deploy-meta-1.0.0.jar"
    jar_content = b"fake jar content for deploy test"
    status = _put_file(f"{deploy_prefix}/{jar_path}", jar_content)
    assert status == 201

    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/1/")

    # Autopublish should have created a publication for version 1
    pubs = maven_publication_api_client.list(repository=repo.pulp_href)
    assert pubs.count == 1

    # Now push a maven-metadata.xml via the deploy API (creates repo version 2)
    metadata_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b"<metadata>"
        b"<groupId>com." + uid.encode() + b"</groupId>"
        b"<artifactId>deploy-meta</artifactId>"
        b"<versioning>"
        b"<latest>1.0.0</latest>"
        b"<release>1.0.0</release>"
        b"<versions><version>1.0.0</version></versions>"
        b"</versioning>"
        b"</metadata>\n"
    )
    metadata_path = f"com/{uid}/deploy-meta/maven-metadata.xml"
    status = _put_file(f"{deploy_prefix}/{metadata_path}", metadata_xml)
    assert status == 201

    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/2/")

    # Autopublish should have created a second publication for version 2
    pubs = maven_publication_api_client.list(repository=repo.pulp_href)
    assert pubs.count == 2

    # The latest publication should be for version 2
    latest_pub = maven_publication_api_client.list(
        repository_version=repo.latest_version_href
    ).results[0]
    assert latest_pub.repository_version == repo.latest_version_href

    # Serve the publication and verify the generated metadata lists the version
    distribution = maven_distribution_factory(publication=latest_pub.pulp_href)
    base_url = distribution_base_url(distribution.base_url)
    metadata_url = urljoin(base_url, f"com/{uid}/deploy-meta/maven-metadata.xml")
    downloaded = download_file(metadata_url)
    assert downloaded.response_obj.status == 200

    root = ElementTree.fromstring(downloaded.body)
    assert root.findtext("groupId") == f"com.{uid}"
    assert root.findtext("artifactId") == "deploy-meta"
    versions = [v.text for v in root.findall(".//versions/version")]
    assert "1.0.0" in versions


@pytest.mark.parallel
def test_autopublish_disabled_by_default(
    maven_publication_api_client,
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
):
    """Test that autopublish is disabled by default."""
    repo = maven_repo_factory()
    uid = _uid()
    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.autopublish is False

    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"com/{uid}/no-auto/1.0.0/no-auto-1.0.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    pubs = maven_publication_api_client.list(repository=repo.pulp_href)
    assert pubs.count == 0
