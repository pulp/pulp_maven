import hashlib
from urllib.parse import urljoin

from pulp_maven.tests.functional.utils import download_file


def test_upload_workflow(
    maven_repo_api_client,
    maven_repo_factory,
    random_maven_artifact_factory,
    maven_artifact_api_client,
    maven_distribution_factory,
    gen_object_with_cleanup,
):
    # Create a repository and assert that the latest version is 0
    repo = maven_repo_factory()
    assert repo.latest_version_href.endswith("/versions/0/")

    # Create a random jar
    jar_file = random_maven_artifact_factory()

    # Upload the jar into the repository
    artifact_kwargs = dict(
        group_id=jar_file["group_id"],
        artifact_id=jar_file["artifact_id"],
        version=jar_file["version"],
        filename=jar_file["filename"],
        file=jar_file["full_path"],
        repository=repo.pulp_href,
    )
    maven_artifact = gen_object_with_cleanup(maven_artifact_api_client, **artifact_kwargs)

    # Assert that a Maven Artifact was created
    assert maven_artifact.group_id == jar_file["group_id"]
    assert maven_artifact.artifact_id == jar_file["artifact_id"]
    assert maven_artifact.version == jar_file["version"]
    assert maven_artifact.filename == jar_file["filename"]

    # Assert that a repository version was created
    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/1/")

    # Assert that this Maven Artifact is in the repository version
    content_in_repo_version = maven_artifact_api_client.list(
        repository_version=repo.latest_version_href
    )
    assert content_in_repo_version.results[0].pulp_href == maven_artifact.pulp_href

    # Create a second repository and assert that latest version is 0
    repo2 = maven_repo_factory()
    assert repo2.latest_version_href.endswith("/versions/0/")

    # Assert that the same content unit can be uploaded again
    artifact_kwargs["repository"] = repo2.pulp_href
    maven_artifact2 = gen_object_with_cleanup(maven_artifact_api_client, **artifact_kwargs)

    # Assert that the existing artifact was identified by the upload API.
    assert maven_artifact.pulp_href == maven_artifact2.pulp_href

    # Assert that a new repository version was created.
    repo2 = maven_repo_api_client.read(repo2.pulp_href)
    assert repo2.latest_version_href.endswith("/versions/1/")

    # Assert that this Maven Artifact is in the repository version
    content_in_repo2_version = maven_artifact_api_client.list(
        repository_version=repo2.latest_version_href
    )
    assert content_in_repo2_version.results[0].pulp_href == maven_artifact2.pulp_href

    # Create a distribution and serve repo
    distribution = maven_distribution_factory(repository=repo.pulp_href)

    # Download the jar from the distribution
    unit_path = (
        f"{jar_file['group_id'].replace('.', '/')}/{jar_file['artifact_id']}/"
        f"{jar_file['version']}/{jar_file['filename']}"
    )
    pulp_unit_url = urljoin(distribution.base_url, unit_path)
    downloaded_file = download_file(pulp_unit_url)
    downloaded_file_checksum = hashlib.sha256(downloaded_file.body).hexdigest()

    # Assert that the downloaded file's checksum matches the original
    assert jar_file["sha256"] == downloaded_file_checksum
