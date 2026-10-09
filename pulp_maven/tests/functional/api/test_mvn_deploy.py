import os
import subprocess
from urllib.parse import urljoin

import aiohttp
import pytest
import requests

from pulp_maven.tests.functional.utils import download_file


def _write_maven_settings(tmp_path, bindings_cfg, server_id="pulp"):
    """Write a settings.xml carrying the deploy credentials for ``server_id``.

    The deploy endpoint authenticates, so a build that does not present
    credentials is answered with a challenge and never uploads anything.
    """
    settings_path = tmp_path / "settings.xml"
    settings_path.write_text(
        "<settings>"
        "<servers><server>"
        f"<id>{server_id}</id>"
        f"<username>{bindings_cfg.username}</username>"
        f"<password>{bindings_cfg.password}</password>"
        "</server></servers>"
        "</settings>"
    )
    return settings_path


def test_mvn_deploy_workflow(
    maven_repo_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    tmp_path,
    pulp_settings,
    bindings_cfg,
):
    # Create a repository and distribution pointing to that repository.
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/0/")

    # Deploy the simple project into the snapshot repository
    current_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        # Copy the simple-project to a temporary directory to ensure proper permissions
        subprocess.check_output(
            ["cp", "-r", f"{current_dir}/../../assets/simple-project", f"{tmp_path}/simple-project"]
        )
        # Update pom.xml to point the Snapshots repository to the test repository
        if pulp_settings.DOMAIN_ENABLED:
            escaped_repo_name = rf"default\/{repo.name}"
            repo_name = f"default/{repo.name}"
        else:
            escaped_repo_name = repo.name
            repo_name = repo.name
        subprocess.check_output(
            [
                "sed",
                "-i",
                f"s/maven-snapshots/{escaped_repo_name}/g",
                f"{tmp_path}/simple-project/pom.xml",
            ]
        )
        # Run mvn deploy
        settings_path = _write_maven_settings(tmp_path, bindings_cfg)
        subprocess.run(
            ["mvn", "deploy", "-s", str(settings_path)],
            cwd=f"{tmp_path}/simple-project",
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as e:
        # The command had a non-zero exit code
        msg = e.stdout.decode() + e.stderr.decode()
        pytest.fail(msg)

    # Assert that the latest version is 12 (6 artifacts + 6 metadata including checksums)
    repo = maven_repo_api_client.read(repo.pulp_href)
    assert repo.latest_version_href.endswith("/versions/12/")

    # Assert that you can get the metadata for the simple-project
    pulp_unit_url = urljoin(
        "http://localhost",
        f"/pulp/maven/{repo_name}/org/sonatype/nexus/examples/simple-project/maven-metadata.xml",
    )
    # Reading through the deploy path is authorized too, so this read-back needs
    # credentials just as the upload did.
    downloaded_file = download_file(
        pulp_unit_url,
        auth=aiohttp.BasicAuth(bindings_cfg.username, bindings_cfg.password),
    )
    assert downloaded_file.response_obj.status == 200

    # Assert that a GET to the Maven API redirects to the content app
    assert not downloaded_file.response_obj.real_url.path.startswith("/pulp/maven")


def _deploy_url(pulp_settings, repo_name, path):
    base = pulp_settings.CONTENT_ORIGIN.rstrip("/")
    if pulp_settings.DOMAIN_ENABLED:
        return f"{base}/pulp/maven/default/{repo_name}/{path}"
    return f"{base}/pulp/maven/{repo_name}/{path}"


ARTIFACT_PATH = "com/example/guarded/1.0.0/guarded-1.0.0.jar"


def test_deploy_rejects_anonymous_upload_with_a_challenge(
    maven_repo_factory, maven_distribution_factory, pulp_settings
):
    """An unauthenticated upload must be refused, and refused in a way clients act on.

    The refusal has to carry an authentication challenge: Ivy-based build tools
    only send credentials once challenged, so answering without one leaves them
    unable to authenticate at all.
    """
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)

    response = requests.put(
        _deploy_url(pulp_settings, repo.name, ARTIFACT_PATH), data=b"anonymous", verify=False
    )

    assert response.status_code == 401
    assert "www-authenticate" in {k.lower() for k in response.headers}


def test_deploy_rejects_authenticated_user_without_permission(
    maven_repo_factory, maven_distribution_factory, pulp_settings, gen_user
):
    """Authenticating is not enough -- the user needs permission on that repository."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)

    user = gen_user()
    response = requests.put(
        _deploy_url(pulp_settings, repo.name, ARTIFACT_PATH),
        data=b"unprivileged",
        auth=(user.username, user.password),
        verify=False,
    )

    assert response.status_code == 403


def test_deploy_rejects_unknown_repository_without_leaking_a_way_in(pulp_settings, gen_user):
    """A path naming no existing repository must be refused, not allowed through.

    Resolving the repository from the URL means a name that matches nothing has to
    fail closed; treating "no repository" as "nothing to check" would make a typo
    a way past the permission check on an endpoint that creates content.
    """
    user = gen_user()
    response = requests.put(
        _deploy_url(pulp_settings, "no-such-repository", ARTIFACT_PATH),
        data=b"unprivileged",
        auth=(user.username, user.password),
        verify=False,
    )

    assert response.status_code in (403, 404)
