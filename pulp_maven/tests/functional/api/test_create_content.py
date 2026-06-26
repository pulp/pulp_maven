import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import pytest

from pulpcore.client.pulp_maven.exceptions import ApiException

from pulp_maven.tests.functional.utils import download_file

FILENAMES = [
    "spring-cloud-config-server-4.3.0-redhat-1.jar",
    "spring-cloud-config-server-4.3.0-redhat-1.jar.md5",
    "spring-cloud-config-server-4.3.0-redhat-1.jar.sha1",
    "spring-cloud-config-server-4.3.0-redhat-1.jar.sha256",
    "spring-cloud-config-server-4.3.0-redhat-1.pom",
    "spring-cloud-config-server-4.3.0-redhat-1.pom.md5",
    "spring-cloud-config-server-4.3.0-redhat-1.pom.sha1",
    "spring-cloud-config-server-4.3.0-redhat-1.pom.sha256",
    "spring-cloud-config-server-4.3.0-redhat-1-cyclonedx.json",
    "spring-cloud-config-server-4.3.0-redhat-1-cyclonedx.json.md5",
    "spring-cloud-config-server-4.3.0-redhat-1-cyclonedx.json.sha1",
    "spring-cloud-config-server-4.3.0-redhat-1-cyclonedx.json.sha256",
    "spring-cloud-config-server-4.3.0-redhat-1-provenance.json",
    "spring-cloud-config-server-4.3.0-redhat-1-provenance.json.md5",
    "spring-cloud-config-server-4.3.0-redhat-1-provenance.json.sha1",
    "spring-cloud-config-server-4.3.0-redhat-1-provenance.json.sha256",
    "spring-cloud-config-server-4.3.0-redhat-1-vex.json",
    "spring-cloud-config-server-4.3.0-redhat-1-vex.json.md5",
    "spring-cloud-config-server-4.3.0-redhat-1-vex.json.sha1",
    "spring-cloud-config-server-4.3.0-redhat-1-vex.json.sha256",
]

GROUP_PATH = "org/springframework/cloud/spring-cloud-config-server/4.3.0-redhat-1"
EXPECTED_GROUP_ID = "org.springframework.cloud"
EXPECTED_ARTIFACT_ID = "spring-cloud-config-server"
EXPECTED_VERSION = "4.3.0-redhat-1"


@pytest.mark.parallel
def test_create_maven_artifacts(
    maven_artifact_api_client,
    random_artifact_factory,
    maven_repo_factory,
    maven_distribution_factory,
    distribution_base_url,
):
    """Test creating MavenArtifact content units and downloading them."""
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    artifact_data = {}
    for filename in FILENAMES:
        artifact = random_artifact_factory(size=64)
        artifact_data[filename] = artifact.sha256
        relative_path = f"{GROUP_PATH}/{filename}"
        content = maven_artifact_api_client.create(
            artifact=artifact.pulp_href,
            relative_path=relative_path,
            repository=repo.pulp_href,
        )
        assert content.pulp_href is not None
        content = maven_artifact_api_client.read(content.pulp_href)
        assert content.group_id == EXPECTED_GROUP_ID
        assert content.artifact_id == EXPECTED_ARTIFACT_ID
        assert content.version == EXPECTED_VERSION
        assert content.filename == filename

    for filename, expected_sha256 in artifact_data.items():
        unit_url = urljoin(base_url, f"{GROUP_PATH}/{filename}")
        downloaded = download_file(unit_url)
        assert downloaded.response_obj.status == 200
        actual_sha256 = hashlib.sha256(downloaded.body).hexdigest()
        assert actual_sha256 == expected_sha256


@pytest.mark.parallel
def test_create_maven_artifact_rhlw_version(
    maven_artifact_api_client,
    random_artifact_factory,
    maven_repo_factory,
    maven_distribution_factory,
    distribution_base_url,
):
    """Test creating a MavenArtifact with an rhlw-style version string."""
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    filename = "spring-security-core-5.3.17.rhlw-00001.jar"
    relative_path = (
        "org/springframework/security/spring-security-core/5.3.17.rhlw-00001/" + filename
    )
    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.create(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
        repository=repo.pulp_href,
    )
    content = maven_artifact_api_client.read(content.pulp_href)
    assert content.group_id == "org.springframework.security"
    assert content.artifact_id == "spring-security-core"
    assert content.version == "5.3.17.rhlw-00001"
    assert content.filename == filename

    unit_url = urljoin(base_url, relative_path)
    downloaded = download_file(unit_url)
    assert downloaded.response_obj.status == 200
    assert hashlib.sha256(downloaded.body).hexdigest() == artifact.sha256


@pytest.mark.parallel
def test_create_maven_artifact_text_prefixed_version(
    maven_artifact_api_client,
    random_artifact_factory,
    maven_repo_factory,
    maven_distribution_factory,
    distribution_base_url,
):
    """Test creating a MavenArtifact with a text-prefixed version string."""
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    filename = "my-lib-final-1.2.3.jar"
    relative_path = f"com/example/my-lib/final-1.2.3/{filename}"
    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.create(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
        repository=repo.pulp_href,
    )
    content = maven_artifact_api_client.read(content.pulp_href)
    assert content.group_id == "com.example"
    assert content.artifact_id == "my-lib"
    assert content.version == "final-1.2.3"
    assert content.filename == filename

    unit_url = urljoin(base_url, relative_path)
    downloaded = download_file(unit_url)
    assert downloaded.response_obj.status == 200
    assert hashlib.sha256(downloaded.body).hexdigest() == artifact.sha256


@pytest.mark.parallel
def test_create_maven_artifact_with_file_upload(
    maven_artifact_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    distribution_base_url,
    tmp_path,
):
    """Test creating a MavenArtifact by uploading a file directly."""
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    filename = "my-library-1.0.0.jar"
    relative_path = f"com/example/my-library/1.0.0/{filename}"

    file_content = os.urandom(128)
    temp_file = tmp_path / filename
    temp_file.write_bytes(file_content)
    expected_sha256 = hashlib.sha256(file_content).hexdigest()

    content = maven_artifact_api_client.create(
        relative_path=relative_path,
        file=str(temp_file),
        repository=repo.pulp_href,
    )
    content = maven_artifact_api_client.read(content.pulp_href)
    assert content.group_id == "com.example"
    assert content.artifact_id == "my-library"
    assert content.version == "1.0.0"
    assert content.filename == filename

    unit_url = urljoin(base_url, relative_path)
    downloaded = download_file(unit_url)
    assert downloaded.response_obj.status == 200
    assert hashlib.sha256(downloaded.body).hexdigest() == expected_sha256


@pytest.mark.parallel
def test_create_maven_artifacts_parallel(
    maven_artifact_api_client,
    random_artifact_factory,
    maven_repo_factory,
    pulp_settings,
):
    """Test parallel uploads to the same repo succeed or return 429 (never 500)."""
    if pulp_settings.WORKER_TYPE != "redis":
        pytest.skip("Immediate tasks require WORKER_TYPE=redis")

    repo = maven_repo_factory()

    artifacts = []
    for i in range(20):
        artifact = random_artifact_factory(size=64)
        filename = f"lib-{i}-1.0.0.jar"
        relative_path = f"com/example/lib-{i}/1.0.0/{filename}"
        artifacts.append((artifact.pulp_href, relative_path))

    throttled_count = 0
    success_count = 0

    def upload(artifact_href, relative_path):
        return maven_artifact_api_client.create(
            artifact=artifact_href,
            relative_path=relative_path,
            repository=repo.pulp_href,
        )

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(upload, href, path): (href, path) for href, path in artifacts}
        for future in as_completed(futures):
            try:
                future.result()
                success_count += 1
            except ApiException as e:
                if e.status == 429:
                    throttled_count += 1
                else:
                    raise

    assert success_count + throttled_count == 20
    assert success_count >= 1
    assert throttled_count >= 1, "Expected at least one 429 response from parallel uploads"
