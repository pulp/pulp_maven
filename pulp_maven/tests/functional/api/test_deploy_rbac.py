"""Tests that the Maven deploy API enforces authentication and RBAC.

See https://github.com/pulp/pulp_maven/issues/528 — the deploy endpoint used to
disable authentication and permissions entirely, allowing anonymous uploads.
"""

import uuid
from urllib.parse import urlsplit

import pytest
import requests
import urllib3

# The dev/CI deployment serves over https with a self-signed cert; match the other
# functional tests (download_file uses verify_ssl=False) and silence the warning.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@pytest.fixture
def deploy_base(bindings_cfg):
    """Scheme + host of the running Pulp instance, derived from the bindings config."""
    parts = urlsplit(bindings_cfg.host)
    return f"{parts.scheme}://{parts.netloc}"


def _deploy_url(deploy_base, repo_name, pulp_settings, rel_path):
    prefix = "default/" if pulp_settings.DOMAIN_ENABLED else ""
    return f"{deploy_base}/pulp/maven/{prefix}{repo_name}/{rel_path}"


def _anonymous_request(method, url, **kwargs):
    """Issue a request with NO credentials.

    requests reads ~/.netrc by default, and the CI runner has admin credentials
    there — so a plain requests.put() is silently authenticated as admin. A session
    with trust_env=False ignores ~/.netrc (and proxy env), keeping the request truly
    unauthenticated.
    """
    session = requests.Session()
    session.trust_env = False
    return session.request(method, url, verify=False, **kwargs)


@pytest.mark.parallel
def test_anonymous_put_returns_401_with_basic_challenge(
    deploy_base, maven_repo_factory, maven_distribution_factory, pulp_settings
):
    """An unauthenticated upload is rejected with 401 and a Basic challenge."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    url = _deploy_url(deploy_base, repo.name, pulp_settings, f"com/ex/{uuid.uuid4()}/1.0/a-1.0.jar")
    resp = _anonymous_request("PUT", url, data=b"content")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").lower().startswith("basic")


@pytest.mark.parallel
def test_authenticated_without_perm_returns_403(
    deploy_base, gen_user, maven_repo_factory, maven_distribution_factory, pulp_settings
):
    """An authenticated user lacking modify perms is forbidden (artifact and metadata)."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    user = gen_user()
    auth = (user.username, user.password)
    jar = _deploy_url(deploy_base, repo.name, pulp_settings, f"com/ex/{uuid.uuid4()}/1.0/a-1.0.jar")
    assert requests.put(jar, data=b"c", auth=auth, verify=False).status_code == 403
    # metadata path (.xml) is gated the same way
    meta = _deploy_url(deploy_base, repo.name, pulp_settings, "com/ex/maven-metadata.xml")
    assert requests.put(meta, data=b"<x/>", auth=auth, verify=False).status_code == 403


@pytest.mark.parallel
def test_user_with_repo_perm_can_deploy(
    deploy_base,
    gen_user,
    maven_repo_factory,
    maven_distribution_factory,
    maven_repo_api_client,
    pulp_settings,
):
    """A user with object-level modify perms on the repo can deploy."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    user = gen_user(object_roles=[("maven.mavenrepository_owner", repo.pulp_href)])
    url = _deploy_url(deploy_base, repo.name, pulp_settings, f"com/ex/{uuid.uuid4()}/1.0/a-1.0.jar")
    resp = requests.put(url, data=b"jar-bytes", auth=(user.username, user.password), verify=False)
    assert resp.status_code == 201
    repo = maven_repo_api_client.read(repo.pulp_href)
    assert not repo.latest_version_href.rstrip("/").endswith("versions/0")


@pytest.mark.parallel
def test_user_with_model_perm_can_deploy(
    deploy_base, gen_user, maven_repo_factory, maven_distribution_factory, pulp_settings
):
    """A user with model-level modify perms can deploy (grant breadth)."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    user = gen_user(model_roles=["maven.mavenrepository_owner"])
    url = _deploy_url(deploy_base, repo.name, pulp_settings, f"com/ex/{uuid.uuid4()}/1.0/a-1.0.jar")
    resp = requests.put(url, data=b"jar-bytes", auth=(user.username, user.password), verify=False)
    assert resp.status_code == 201


@pytest.mark.parallel
def test_anonymous_get_is_not_auth_gated(
    deploy_base, maven_repo_factory, maven_distribution_factory, pulp_settings
):
    """Reads stay anonymous: a GET for a missing path is 404, not 401/403."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    url = _deploy_url(deploy_base, repo.name, pulp_settings, "com/ex/missing/1.0/missing-1.0.jar")
    resp = _anonymous_request("GET", url, allow_redirects=False)
    assert resp.status_code == 404
