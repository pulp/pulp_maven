import hashlib
import os
import uuid
from urllib.parse import urljoin

import pytest

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
def test_upload_maven_artifacts(
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    maven_repo_factory,
    maven_distribution_factory,
    distribution_base_url,
    monitor_task,
):
    """Test uploading MavenArtifact content units synchronously with labels."""
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    run_id = str(uuid.uuid4())
    created_hrefs = []
    artifact_data = {}
    for filename in FILENAMES:
        artifact = random_artifact_factory(size=64)
        artifact_data[filename] = artifact.sha256
        relative_path = f"{GROUP_PATH}/{filename}"

        labels = {"run_id": run_id}
        base = filename.split(".md5")[0].split(".sha1")[0].split(".sha256")[0]
        if base.endswith(".jar"):
            labels["type"] = "jar"
        elif base.endswith(".pom"):
            labels["type"] = "pom"
        elif "cyclonedx" in base:
            labels["type"] = "sbom"
        elif "provenance" in base:
            labels["type"] = "provenance"
        elif "vex" in base:
            labels["type"] = "vex"

        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=relative_path,
            pulp_labels=labels,
        )
        assert content.pulp_href is not None
        assert content.group_id == EXPECTED_GROUP_ID
        assert content.artifact_id == EXPECTED_ARTIFACT_ID
        assert content.version == EXPECTED_VERSION
        assert content.filename == filename
        assert content.pulp_labels["run_id"] == run_id
        assert content.pulp_labels["type"] in ("jar", "pom", "sbom", "provenance", "vex")
        created_hrefs.append(content.pulp_href)

    # Add all content to repository in one request
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": created_hrefs}).task
    )

    for filename, expected_sha256 in artifact_data.items():
        unit_url = urljoin(base_url, f"{GROUP_PATH}/{filename}")
        downloaded = download_file(unit_url)
        assert downloaded.response_obj.status == 200
        actual_sha256 = hashlib.sha256(downloaded.body).hexdigest()
        assert actual_sha256 == expected_sha256

    # Filter by unique run_id label
    results = maven_artifact_api_client.list(pulp_label_select=f"run_id={run_id}")
    assert results.count == 20

    # Filter by AND: run_id AND type=jar
    results = maven_artifact_api_client.list(pulp_label_select=f"run_id={run_id},type=jar")
    assert results.count == 4  # .jar, .jar.md5, .jar.sha1, .jar.sha256

    # Filter by label key existence
    results = maven_artifact_api_client.list(pulp_label_select=f"run_id={run_id},type")
    assert results.count == 20

    # Filter by contains
    results = maven_artifact_api_client.list(pulp_label_select=f"run_id={run_id},type~sb")
    assert results.count == 4  # cyclonedx.json + its checksum files

    # Filter by OR using q filter
    results = maven_artifact_api_client.list(
        q=f'pulp_label_select="run_id={run_id},type=jar" OR pulp_label_select="run_id={run_id},type=pom"'
    )
    assert results.count == 8  # 4 jar files + 4 pom files


@pytest.mark.parallel
def test_upload_maven_artifact_rhlw_version(
    maven_artifact_api_client,
    random_artifact_factory,
):
    """Test uploading a MavenArtifact with an rhlw-style version string."""
    filename = "spring-security-core-5.3.17.rhlw-00001.jar"
    relative_path = (
        "org/springframework/security/spring-security-core/5.3.17.rhlw-00001/" + filename
    )
    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
    )
    assert content.group_id == "org.springframework.security"
    assert content.artifact_id == "spring-security-core"
    assert content.version == "5.3.17.rhlw-00001"
    assert content.filename == filename


@pytest.mark.parallel
def test_upload_maven_artifact_text_prefixed_version(
    maven_artifact_api_client,
    random_artifact_factory,
):
    """Test uploading a MavenArtifact with a text-prefixed version string."""
    filename = "my-lib-final-1.2.3.jar"
    relative_path = f"com/example/my-lib/final-1.2.3/{filename}"
    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
    )
    assert content.group_id == "com.example"
    assert content.artifact_id == "my-lib"
    assert content.version == "final-1.2.3"
    assert content.filename == filename


@pytest.mark.parallel
def test_upload_maven_artifact_with_file(
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    distribution_base_url,
    monitor_task,
    tmp_path,
):
    """Test uploading a MavenArtifact with a file directly."""
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    filename = "my-library-1.0.0.jar"
    relative_path = f"com/example/my-library/1.0.0/{filename}"

    file_content = os.urandom(128)
    temp_file = tmp_path / filename
    temp_file.write_bytes(file_content)
    expected_sha256 = hashlib.sha256(file_content).hexdigest()

    content = maven_artifact_api_client.upload(
        relative_path=relative_path,
        file=str(temp_file),
    )
    assert content.group_id == "com.example"
    assert content.artifact_id == "my-library"
    assert content.version == "1.0.0"
    assert content.filename == filename

    # Add to repo and verify download
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    unit_url = urljoin(base_url, relative_path)
    downloaded = download_file(unit_url)
    assert downloaded.response_obj.status == 200
    assert hashlib.sha256(downloaded.body).hexdigest() == expected_sha256


@pytest.mark.parallel
def test_async_create_maven_artifact(
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    maven_repo_factory,
    maven_distribution_factory,
    distribution_base_url,
    monitor_task,
):
    """Test creating a MavenArtifact via the async create endpoint with a repository."""
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distribution.base_url)

    artifact = random_artifact_factory(size=64)
    filename = "async-lib-1.0.0.jar"
    relative_path = f"com/example/async-lib/1.0.0/{filename}"

    task = maven_artifact_api_client.create(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
        repository=repo.pulp_href,
    )
    result = monitor_task(task.task)
    content_hrefs = [r for r in result.created_resources if "content/maven/artifact" in r]
    assert len(content_hrefs) == 1

    content = maven_artifact_api_client.read(content_hrefs[0])
    assert content.group_id == "com.example"
    assert content.artifact_id == "async-lib"
    assert content.version == "1.0.0"
    assert content.filename == filename

    unit_url = urljoin(base_url, relative_path)
    downloaded = download_file(unit_url)
    assert downloaded.response_obj.status == 200
    assert hashlib.sha256(downloaded.body).hexdigest() == artifact.sha256


@pytest.mark.parallel
def test_upload_duplicate_maven_artifact(
    maven_artifact_api_client,
    random_artifact_factory,
):
    """Test that uploading the same file returns the existing content unit."""
    artifact = random_artifact_factory(size=64)
    filename = "duplicate-test-1.0.0.jar"
    relative_path = f"com/example/duplicate-test/1.0.0/{filename}"

    first = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
    )
    assert first.pulp_href is not None

    # Upload same artifact again → same content unit (same sha256)
    second = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
    )
    assert second.pulp_href == first.pulp_href

    # Upload different artifact to same path → new content unit (different sha256)
    artifact2 = random_artifact_factory(size=64)
    third = maven_artifact_api_client.upload(
        artifact=artifact2.pulp_href,
        relative_path=relative_path,
    )
    assert third.pulp_href != first.pulp_href


@pytest.mark.parallel
def test_upload_maven_metadata(
    maven_artifact_api_client,
    tmp_path,
):
    """Test uploading maven-metadata.xml with GAV parsed from XML content."""
    xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <groupId>com.fasterxml.jackson.module</groupId>
  <artifactId>jackson-module-parameter-names</artifactId>
  <versioning>
    <latest>2.8.11</latest>
    <release>2.8.11</release>
    <versions>
      <version>2.8.11</version>
    </versions>
  </versioning>
</metadata>
"""
    temp_file = tmp_path / "maven-metadata.xml"
    temp_file.write_bytes(xml_content)

    relative_path = "com/fasterxml/jackson/module/jackson-module-parameter-names/maven-metadata.xml"
    content = maven_artifact_api_client.upload(
        file=str(temp_file),
        relative_path=relative_path,
    )
    assert content.group_id == "com.fasterxml.jackson.module"
    assert content.artifact_id == "jackson-module-parameter-names"
    assert content.version is None
    assert content.filename == "maven-metadata.xml"
    assert content.sha256 is not None


@pytest.mark.parallel
def test_upload_maven_metadata_snapshot(
    maven_artifact_api_client,
    tmp_path,
):
    """Test uploading version-level SNAPSHOT maven-metadata.xml."""
    xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <groupId>com.fasterxml.jackson.module</groupId>
  <artifactId>jackson-module-parameter-names</artifactId>
  <version>2.8.11-SNAPSHOT</version>
  <versioning>
    <snapshot>
      <timestamp>20260627.200810</timestamp>
      <buildNumber>1</buildNumber>
    </snapshot>
  </versioning>
</metadata>
"""
    temp_file = tmp_path / "maven-metadata.xml"
    temp_file.write_bytes(xml_content)

    relative_path = (
        "com/fasterxml/jackson/module/jackson-module-parameter-names"
        "/2.8.11-SNAPSHOT/maven-metadata.xml"
    )
    content = maven_artifact_api_client.upload(
        file=str(temp_file),
        relative_path=relative_path,
    )
    assert content.group_id == "com.fasterxml.jackson.module"
    assert content.artifact_id == "jackson-module-parameter-names"
    assert content.version == "2.8.11-SNAPSHOT"
    assert content.filename == "maven-metadata.xml"
    assert content.sha256 is not None


@pytest.mark.parallel
def test_upload_invalid_xml_metadata(
    maven_artifact_api_client,
    tmp_path,
):
    """Test that uploading invalid XML as maven-metadata.xml returns 400, not 500."""
    temp_file = tmp_path / "maven-metadata.xml"
    temp_file.write_text("not valid xml")

    relative_path = "com/example/bad-xml/maven-metadata.xml"
    from pulpcore.client.pulp_maven import ApiException

    with pytest.raises(ApiException) as exc_info:
        maven_artifact_api_client.upload(file=str(temp_file), relative_path=relative_path)
    assert exc_info.value.status == 400


@pytest.mark.parallel
def test_upload_duplicate_maven_metadata(
    maven_artifact_api_client,
    tmp_path,
):
    """Test that uploading the exact same maven-metadata.xml returns the existing content unit."""
    xml_content = b'<?xml version="1.0"?>\n<metadata><groupId>com.example</groupId><artifactId>dup-meta-test</artifactId><versioning><latest>1.0.0</latest><versions><version>1.0.0</version></versions></versioning></metadata>'

    relative_path = "com/example/dup-meta-test/maven-metadata.xml"

    f1 = tmp_path / "maven-metadata-1.xml"
    f1.write_bytes(xml_content)
    first = maven_artifact_api_client.upload(file=str(f1), relative_path=relative_path)

    # Upload identical content again
    f2 = tmp_path / "maven-metadata-2.xml"
    f2.write_bytes(xml_content)
    second = maven_artifact_api_client.upload(file=str(f2), relative_path=relative_path)

    assert second.pulp_href == first.pulp_href


@pytest.mark.parallel
def test_repo_key_fields_replace_duplicate_metadata(
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    monitor_task,
    tmp_path,
):
    """Test that metadata with same GAV+filename replaces old metadata in repository."""
    repo = maven_repo_factory()
    relative_path = "com/example/dedup-test/maven-metadata.xml"

    # Upload first metadata
    xml1 = b'<?xml version="1.0"?>\n<metadata><groupId>com.example</groupId><artifactId>dedup-test</artifactId><versioning><latest>1.0.0</latest><versions><version>1.0.0</version></versions></versioning></metadata>'
    f1 = tmp_path / "maven-metadata-v1.xml"
    f1.write_bytes(xml1)
    content1 = maven_artifact_api_client.upload(file=str(f1), relative_path=relative_path)

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content1.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/1/")

    # Upload second metadata with same GAV+filename but different content (new version added)
    xml2 = b'<?xml version="1.0"?>\n<metadata><groupId>com.example</groupId><artifactId>dedup-test</artifactId><versioning><latest>2.0.0</latest><versions><version>1.0.0</version><version>2.0.0</version></versions></versioning></metadata>'
    f2 = tmp_path / "maven-metadata-v2.xml"
    f2.write_bytes(xml2)
    content2 = maven_artifact_api_client.upload(file=str(f2), relative_path=relative_path)

    # Different sha256 → different content unit
    assert content2.pulp_href != content1.pulp_href

    # Add to repo — repo_key_fields should replace the old metadata
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content2.pulp_href]}
        ).task
    )
    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/2/")

    # Verify only 1 metadata in repo (the new one replaced the old)
    results = maven_artifact_api_client.list(repository_version=repo.latest_version_href)
    assert results.count == 1
    assert results.results[0].pulp_href == content2.pulp_href
