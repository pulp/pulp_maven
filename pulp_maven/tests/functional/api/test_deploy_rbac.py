"""Tests that the Maven deploy API enforces authentication and RBAC.

See https://github.com/pulp/pulp_maven/issues/528 — the deploy endpoint used to
disable authentication and permissions entirely, allowing anonymous uploads.
"""

import uuid

import pytest
import requests


def _deploy_url(repo_name, pulp_settings, rel_path):
    prefix = "default/" if pulp_settings.DOMAIN_ENABLED else ""
    return f"http://localhost/pulp/maven/{prefix}{repo_name}/{rel_path}"


@pytest.mark.parallel
def test_anonymous_put_returns_401_with_basic_challenge(
    maven_repo_factory, maven_distribution_factory, pulp_settings
):
    """An unauthenticated upload is rejected with 401 and a Basic challenge."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    url = _deploy_url(repo.name, pulp_settings, f"com/ex/{uuid.uuid4()}/1.0/a-1.0.jar")
    resp = requests.put(url, data=b"content")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").lower().startswith("basic")


@pytest.mark.parallel
def test_authenticated_without_perm_returns_403(
    gen_user, maven_repo_factory, maven_distribution_factory, pulp_settings
):
    """An authenticated user lacking modify perms is forbidden (artifact and metadata)."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    user = gen_user()
    jar = _deploy_url(repo.name, pulp_settings, f"com/ex/{uuid.uuid4()}/1.0/a-1.0.jar")
    assert requests.put(jar, data=b"c", auth=(user.username, user.password)).status_code == 403
    # metadata path (.xml) is gated the same way
    meta = _deploy_url(repo.name, pulp_settings, "com/ex/maven-metadata.xml")
    assert requests.put(meta, data=b"<x/>", auth=(user.username, user.password)).status_code == 403


@pytest.mark.parallel
def test_user_with_repo_perm_can_deploy(
    gen_user, maven_repo_factory, maven_distribution_factory, maven_repo_api_client, pulp_settings
):
    """A user with object-level modify perms on the repo can deploy."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    user = gen_user(object_roles=[("maven.mavenrepository_owner", repo.pulp_href)])
    url = _deploy_url(repo.name, pulp_settings, f"com/ex/{uuid.uuid4()}/1.0/a-1.0.jar")
    resp = requests.put(url, data=b"jar-bytes", auth=(user.username, user.password))
    assert resp.status_code == 201
    repo = maven_repo_api_client.read(repo.pulp_href)
    assert not repo.latest_version_href.rstrip("/").endswith("versions/0")


@pytest.mark.parallel
def test_user_with_model_perm_can_deploy(
    gen_user, maven_repo_factory, maven_distribution_factory, pulp_settings
):
    """A user with model-level modify perms can deploy (grant breadth)."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    user = gen_user(model_roles=["maven.mavenrepository_owner"])
    url = _deploy_url(repo.name, pulp_settings, f"com/ex/{uuid.uuid4()}/1.0/a-1.0.jar")
    resp = requests.put(url, data=b"jar-bytes", auth=(user.username, user.password))
    assert resp.status_code == 201


@pytest.mark.parallel
def test_anonymous_get_is_not_auth_gated(
    maven_repo_factory, maven_distribution_factory, pulp_settings
):
    """Reads stay anonymous: a GET for a missing path is 404, not 401/403."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    url = _deploy_url(repo.name, pulp_settings, "com/ex/missing/1.0/missing-1.0.jar")
    resp = requests.get(url, allow_redirects=False)
    assert resp.status_code == 404
