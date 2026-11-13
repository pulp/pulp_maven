"""Re-publishing the same coordinates with different content."""

import hashlib
from urllib.parse import urlsplit

import requests

from pulp_maven.tests.functional.utils import download_file

ARTIFACT_PATH = "com/example/redeploy/1.0.0-SNAPSHOT/redeploy-1.0.0-SNAPSHOT.jar"
FIRST = b"first build of this snapshot"
SECOND = b"second build of this snapshot, different bytes"


def _deploy_url(bindings_cfg, pulp_settings, repo_name, path=ARTIFACT_PATH):
    parts = urlsplit(bindings_cfg.host)
    prefix = "default/" if pulp_settings.DOMAIN_ENABLED else ""
    return f"{parts.scheme}://{parts.netloc}/pulp/maven/{prefix}{repo_name}/{path}"


def _content_url(distribution, path=ARTIFACT_PATH):
    return f"{distribution.base_url.rstrip('/')}/{path}"


def _deploy(url, data, bindings_cfg):
    """PUT to the deploy endpoint with credentials.

    Sent unconditionally so this reads the same whether or not the endpoint
    enforces authentication.
    """
    return requests.put(
        url, data=data, auth=(bindings_cfg.username, bindings_cfg.password), verify=False
    )


def test_redeploy_with_new_content_serves_the_new_content(
    maven_repo_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    pulp_settings,
    bindings_cfg,
):
    """A second deploy to the same coordinates must not leave the first file in place.

    Snapshot development republishes the same coordinates repeatedly. Keeping the
    original file while answering the upload with a success status makes that
    workflow fail silently: the publisher is told the build was uploaded, and
    everyone downstream keeps resolving the previous one.
    """
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    url = _deploy_url(bindings_cfg, pulp_settings, repo.name)

    assert _deploy(url, FIRST, bindings_cfg).status_code == 201
    downloaded = download_file(_content_url(distribution))
    assert hashlib.sha256(downloaded.body).hexdigest() == hashlib.sha256(FIRST).hexdigest()

    assert _deploy(url, SECOND, bindings_cfg).status_code == 201
    downloaded = download_file(_content_url(distribution))
    assert hashlib.sha256(downloaded.body).hexdigest() == hashlib.sha256(SECOND).hexdigest()


def test_redeploy_of_identical_content_does_not_create_a_repository_version(
    maven_repo_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    pulp_settings,
    bindings_cfg,
):
    """Re-uploading the same bytes stays a no-op.

    A build that deploys unchanged output should not accumulate repository
    versions, which is why the checksum is what distinguishes one upload from
    another rather than the upload simply always replacing.
    """
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    url = _deploy_url(bindings_cfg, pulp_settings, repo.name)

    assert _deploy(url, FIRST, bindings_cfg).status_code == 201
    first_version = maven_repo_api_client.read(repo.pulp_href).latest_version_href

    assert _deploy(url, FIRST, bindings_cfg).status_code == 201
    assert maven_repo_api_client.read(repo.pulp_href).latest_version_href == first_version


def test_redeployed_repository_holds_one_artifact_per_coordinate(
    maven_repo_api_client,
    maven_artifact_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    pulp_settings,
    bindings_cfg,
):
    """The superseded file must leave the repository rather than sit alongside it.

    Two units for one path in the same repository version would make what gets
    served depend on ordering. Both units still exist as content, distinct by
    checksum, but only the newer one belongs to the repository.
    """
    repo = maven_repo_factory()
    distribution = maven_distribution_factory(repository=repo.pulp_href)
    url = _deploy_url(bindings_cfg, pulp_settings, repo.name)

    _deploy(url, FIRST, bindings_cfg)
    _deploy(url, SECOND, bindings_cfg)

    latest = maven_repo_api_client.read(repo.pulp_href).latest_version_href

    in_repository = maven_artifact_api_client.list(
        repository_version=latest, filename="redeploy-1.0.0-SNAPSHOT.jar"
    )
    assert in_repository.count == 1

    everywhere = maven_artifact_api_client.list(filename="redeploy-1.0.0-SNAPSHOT.jar")
    assert everywhere.count == 2

    # Identify the surviving unit by what the repository actually serves rather
    # than by reading the checksum field back, so this does not depend on the
    # generated client having been rebuilt against the current schema.
    served = download_file(_content_url(distribution))
    assert hashlib.sha256(served.body).hexdigest() == hashlib.sha256(SECOND).hexdigest()
