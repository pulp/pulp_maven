"""Concurrent deploys into one repository."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests

UPLOADS = 8


def _deploy_url(bindings_cfg, pulp_settings, repo_name, path):
    parts = urlsplit(bindings_cfg.host)
    prefix = "default/" if pulp_settings.DOMAIN_ENABLED else ""
    return f"{parts.scheme}://{parts.netloc}/pulp/maven/{prefix}{repo_name}/{path}"


def test_concurrent_deploys_to_one_repository_all_succeed(
    maven_repo_api_client,
    maven_artifact_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    pulp_settings,
    bindings_cfg,
):
    """Uploads that arrive while another one holds the repository wait for their turn.

    Builds that publish into one repository at the same time, such as two CI pipelines or
    a build that deploys its modules in parallel, overlap their uploads, and Maven clients
    do not retry a refused upload. Refusing every upload that overlaps another one would
    fail those builds outright.
    """
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    artifact_id = f"concurrent-{uuid.uuid4()}"
    paths = [f"com/example/{artifact_id}/1.0.{i}/{artifact_id}-1.0.{i}.jar" for i in range(UPLOADS)]

    def deploy(path):
        return requests.put(
            _deploy_url(bindings_cfg, pulp_settings, repo.name, path),
            data=f"jar {path}".encode(),
            auth=(bindings_cfg.username, bindings_cfg.password),
            verify=False,
        ).status_code

    with ThreadPoolExecutor(max_workers=UPLOADS) as pool:
        statuses = list(pool.map(deploy, paths))

    assert statuses == [201] * UPLOADS
    repo = maven_repo_api_client.read(repo.pulp_href)
    deployed = maven_artifact_api_client.list(
        repository_version=repo.latest_version_href, artifact_id=artifact_id
    )
    assert deployed.count == UPLOADS
