"""Deploys recorded as pending and added to the repository in batches (MAVEN_DEPLOY_BATCH)."""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import pytest
import requests

from pulp_maven.tests.functional.utils import download_file


@pytest.fixture(autouse=True)
def _batch_mode_only(pulp_settings):
    if not pulp_settings.MAVEN_DEPLOY_BATCH:
        pytest.skip("The server does not batch deploys (MAVEN_DEPLOY_BATCH is off).")


@pytest.fixture
def deploy(bindings_cfg, pulp_settings):
    parts = urlsplit(bindings_cfg.host)
    prefix = "default/" if pulp_settings.DOMAIN_ENABLED else ""

    def _deploy(repo_name, path, data):
        return requests.put(
            f"{parts.scheme}://{parts.netloc}/pulp/maven/{prefix}{repo_name}/{path}",
            data=data,
            auth=(bindings_cfg.username, bindings_cfg.password),
            verify=False,
        ).status_code

    return _deploy


def _committed(pulp_settings):
    """Seconds after the last deploy within which a batch is committed, with room to spare."""
    return pulp_settings.MAVEN_DEPLOY_BATCH_LINGER + pulp_settings.MAVEN_DEPLOY_BATCH_MAX_WAIT + 20


def _wait_for(check, timeout):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "the deployed files were not committed in time"
        time.sleep(0.5)


def _version_number(maven_repo_api_client, repo):
    href = maven_repo_api_client.read(repo.pulp_href).latest_version_href
    return int(href.rstrip("/").rsplit("/", 1)[1])


def _artifact_count(maven_repo_api_client, maven_artifact_api_client, repo, artifact_id):
    href = maven_repo_api_client.read(repo.pulp_href).latest_version_href
    return maven_artifact_api_client.list(repository_version=href, artifact_id=artifact_id).count


def test_sequential_deploys_share_versions(
    deploy,
    maven_repo_api_client,
    maven_artifact_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    pulp_settings,
):
    """A publish of many files, one after another, lands in far fewer versions than files.

    Each deploy answers at once; the files appear together once uploads pause.
    """
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    artifact_id = f"seq-{uuid.uuid4()}"
    paths = [
        f"com/example/{artifact_id}/1.0.0/{artifact_id}-1.0.0{suffix}"
        for suffix in (".jar", ".jar.md5", ".jar.sha1", ".pom", ".pom.md5", ".pom.sha1")
    ]

    for path in paths:
        assert deploy(repo.name, path, f"bytes of {path}".encode()) == 201

    _wait_for(
        lambda: _artifact_count(maven_repo_api_client, maven_artifact_api_client, repo, artifact_id)
        == len(paths),
        _committed(pulp_settings),
    )
    assert _version_number(maven_repo_api_client, repo) < len(paths)
    assert not maven_repo_api_client.read(repo.pulp_href).latest_version_href.endswith("/0/")


def test_concurrent_deploys_all_land(
    deploy,
    maven_repo_api_client,
    maven_artifact_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    pulp_settings,
):
    """Uploads arriving at the same time all answer 201 and all end up in the repository."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    artifact_id = f"par-{uuid.uuid4()}"
    paths = [f"com/example/{artifact_id}/1.0.{i}/{artifact_id}-1.0.{i}.jar" for i in range(8)]

    with ThreadPoolExecutor(max_workers=len(paths)) as pool:
        statuses = list(pool.map(lambda p: deploy(repo.name, p, p.encode()), paths))

    assert statuses == [201] * len(paths)
    _wait_for(
        lambda: _artifact_count(maven_repo_api_client, maven_artifact_api_client, repo, artifact_id)
        == len(paths),
        _committed(pulp_settings),
    )


def test_deployed_file_is_served_before_its_batch_is_committed(
    deploy,
    maven_repo_api_client,
    maven_repo_factory,
    maven_distribution_factory,
):
    """A file is readable as soon as its upload is answered, before any version holds it.

    A build that publishes and then resolves what it published would otherwise see a 404
    for as long as the batch waits.
    """
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    artifact_id = f"read-{uuid.uuid4()}"
    path = f"com/example/{artifact_id}/1.0.0/{artifact_id}-1.0.0.jar"

    assert deploy(repo.name, path, b"just deployed") == 201

    served = download_file(f"{distribution.base_url.rstrip('/')}/{path}")
    assert served.body == b"just deployed"
    assert maven_repo_api_client.read(repo.pulp_href).latest_version_href.endswith("/0/")


def test_client_snapshot_metadata_in_a_batch_is_kept(
    deploy,
    maven_repo_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    pulp_settings,
):
    """A SNAPSHOT deployed with timestamped file names keeps the client's maven-metadata.xml.

    That file is what maps the SNAPSHOT version to its timestamped build; it must not be
    replaced by metadata generated from the file names when it lands in the same batch.
    """
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    artifact_id = f"tsnap-{uuid.uuid4()}"
    base = f"com/example/{artifact_id}/1.0-SNAPSHOT"
    build = "1.0-20261028.120000-1"
    metadata = (
        f"<metadata><groupId>com.example</groupId><artifactId>{artifact_id}</artifactId>"
        "<version>1.0-SNAPSHOT</version><versioning><snapshot><timestamp>20261028.120000"
        "</timestamp><buildNumber>1</buildNumber></snapshot><snapshotVersions>"
        f"<snapshotVersion><extension>jar</extension><value>{build}</value>"
        "<updated>20261028120000</updated></snapshotVersion></snapshotVersions>"
        "<lastUpdated>20261028120000</lastUpdated></versioning></metadata>"
    ).encode()

    assert deploy(repo.name, f"{base}/{artifact_id}-{build}.jar", b"timestamped build") == 201
    assert deploy(repo.name, f"{base}/maven-metadata.xml", metadata) == 201

    _wait_for(
        lambda: maven_repo_api_client.read(repo.pulp_href).latest_version_href.endswith("/1/"),
        _committed(pulp_settings),
    )
    served = download_file(f"{distribution.base_url.rstrip('/')}/{base}/maven-metadata.xml")
    assert served.body == metadata
