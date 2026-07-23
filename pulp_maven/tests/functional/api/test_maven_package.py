"""Functional tests for MavenPackage lifecycle, API, and integration paths."""

import os
import shutil
import subprocess
import time
from urllib.parse import urljoin

import pytest

from pulp_maven.tests.functional.utils import download_file


@pytest.mark.parallel
def test_pom_upload_creates_package(
    maven_artifact_api_client,
    maven_package_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    pom_file_factory,
    monitor_task,
):
    """Upload a .pom via REST API and verify MavenPackage is created with correct metadata."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)

    pom_path = pom_file_factory(
        group_id="com.example.pkg",
        artifact_id="my-lib",
        version="1.0.0",
        name="My Library",
        packaging="jar",
    )
    relative_path = "com/example/pkg/my-lib/1.0.0/my-lib-1.0.0.pom"

    content = maven_artifact_api_client.upload(
        file=str(pom_path),
        relative_path=relative_path,
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    packages = maven_package_api_client.list(repository_version=repo.latest_version_href)
    assert packages.count == 1

    pkg = packages.results[0]
    assert pkg.group_id == "com.example.pkg"
    assert pkg.artifact_id == "my-lib"
    assert pkg.version == "1.0.0"
    assert pkg.name == "My Library"
    assert pkg.packaging == "jar"


@pytest.mark.parallel
def test_multiple_artifacts_single_package(
    maven_artifact_api_client,
    maven_package_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    random_artifact_factory,
    pom_file_factory,
    monitor_task,
):
    """Upload .jar + .pom + .sha1 for the same GAV and verify exactly one MavenPackage."""
    repo = maven_repo_factory()
    group_path = "com/example/multi/multi-art/2.0.0"

    jar_artifact = random_artifact_factory(size=64)
    jar = maven_artifact_api_client.upload(
        artifact=jar_artifact.pulp_href,
        relative_path=f"{group_path}/multi-art-2.0.0.jar",
    )

    sha1_artifact = random_artifact_factory(size=40)
    sha1 = maven_artifact_api_client.upload(
        artifact=sha1_artifact.pulp_href,
        relative_path=f"{group_path}/multi-art-2.0.0.jar.sha1",
    )

    pom_path = pom_file_factory(
        group_id="com.example.multi",
        artifact_id="multi-art",
        version="2.0.0",
        name="Multi Artifact",
    )
    pom = maven_artifact_api_client.upload(
        file=str(pom_path),
        relative_path=f"{group_path}/multi-art-2.0.0.pom",
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"add_content_units": [jar.pulp_href, sha1.pulp_href, pom.pulp_href]},
        ).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    packages = maven_package_api_client.list(repository_version=repo.latest_version_href)
    assert packages.count == 1
    assert packages.results[0].artifact_id == "multi-art"


@pytest.mark.parallel
def test_remove_all_artifacts_removes_package(
    maven_artifact_api_client,
    maven_package_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    random_artifact_factory,
    pom_file_factory,
    monitor_task,
):
    """Remove all artifacts for a GAV and verify the package is removed from the version."""
    repo = maven_repo_factory()
    group_path = "com/example/rm/rm-lib/1.0.0"

    jar_artifact = random_artifact_factory(size=64)
    jar = maven_artifact_api_client.upload(
        artifact=jar_artifact.pulp_href,
        relative_path=f"{group_path}/rm-lib-1.0.0.jar",
    )

    pom_path = pom_file_factory(
        group_id="com.example.rm",
        artifact_id="rm-lib",
        version="1.0.0",
    )
    pom = maven_artifact_api_client.upload(
        file=str(pom_path),
        relative_path=f"{group_path}/rm-lib-1.0.0.pom",
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"add_content_units": [jar.pulp_href, pom.pulp_href]},
        ).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    packages = maven_package_api_client.list(repository_version=repo.latest_version_href)
    assert packages.count == 1

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"remove_content_units": [jar.pulp_href, pom.pulp_href]},
        ).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    packages = maven_package_api_client.list(repository_version=repo.latest_version_href)
    assert packages.count == 0


@pytest.mark.parallel
def test_no_pom_no_package(
    maven_artifact_api_client,
    maven_package_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    random_artifact_factory,
    monitor_task,
):
    """Upload only a .jar (no .pom) and verify no MavenPackage is created."""
    repo = maven_repo_factory()
    artifact = random_artifact_factory(size=64)

    jar = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path="com/example/nopom/nopom-lib/1.0.0/nopom-lib-1.0.0.jar",
    )

    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [jar.pulp_href]}).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    packages = maven_package_api_client.list(repository_version=repo.latest_version_href)
    assert packages.count == 0


@pytest.mark.parallel
def test_snapshot_redeploy_refreshes_metadata(
    maven_artifact_api_client,
    maven_package_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
):
    """Re-deploy a SNAPSHOT POM with updated metadata and verify the package is refreshed."""
    repo = maven_repo_factory()
    group_path = "com/example/snap/snap-lib/1.0.0-SNAPSHOT"
    relative_path = f"{group_path}/snap-lib-1.0.0-SNAPSHOT.pom"

    pom_v1 = pom_file_factory(
        group_id="com.example.snap",
        artifact_id="snap-lib",
        version="1.0.0-SNAPSHOT",
        name="V1",
    )
    content_v1 = maven_artifact_api_client.upload(
        file=str(pom_v1),
        relative_path=relative_path,
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content_v1.pulp_href]}
        ).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    packages = maven_package_api_client.list(repository_version=repo.latest_version_href)
    assert packages.count == 1
    assert packages.results[0].name == "V1"

    pom_v2 = pom_file_factory(
        group_id="com.example.snap",
        artifact_id="snap-lib",
        version="1.0.0-SNAPSHOT",
        name="V2",
    )
    content_v2 = maven_artifact_api_client.upload(
        file=str(pom_v2),
        relative_path=relative_path,
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content_v2.pulp_href]}
        ).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    packages = maven_package_api_client.list(repository_version=repo.latest_version_href)
    assert packages.count == 1
    assert packages.results[0].name == "V2"


@pytest.mark.parallel
def test_package_api_filtering(
    maven_artifact_api_client,
    maven_package_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
):
    """Upload 3 distinct GAVs and verify filtering by group_id, artifact_id, version, packaging."""
    repo = maven_repo_factory()
    gavs = [
        ("com.example.filter", "lib-a", "1.0.0", "jar"),
        ("com.example.filter", "lib-b", "2.0.0", "war"),
        ("org.other.filter", "lib-c", "3.0.0", "pom"),
    ]

    hrefs = []
    for g, a, v, packaging in gavs:
        group_path = g.replace(".", "/") + f"/{a}/{v}"
        pom_path = pom_file_factory(group_id=g, artifact_id=a, version=v, packaging=packaging)
        content = maven_artifact_api_client.upload(
            file=str(pom_path),
            relative_path=f"{group_path}/{a}-{v}.pom",
        )
        hrefs.append(content.pulp_href)

    monitor_task(maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": hrefs}).task)

    repo = maven_repo_api_client.read(repo.pulp_href)
    rv = repo.latest_version_href

    all_pkgs = maven_package_api_client.list(repository_version=rv)
    assert all_pkgs.count == 3

    by_group = maven_package_api_client.list(repository_version=rv, group_id="com.example.filter")
    assert by_group.count == 2

    by_artifact = maven_package_api_client.list(repository_version=rv, artifact_id="lib-b")
    assert by_artifact.count == 1
    assert by_artifact.results[0].version == "2.0.0"

    by_version = maven_package_api_client.list(repository_version=rv, version="3.0.0")
    assert by_version.count == 1
    assert by_version.results[0].artifact_id == "lib-c"

    by_packaging = maven_package_api_client.list(repository_version=rv, packaging="war")
    assert by_packaging.count == 1
    assert by_packaging.results[0].artifact_id == "lib-b"


@pytest.mark.skipif(shutil.which("mvn") is None, reason="mvn CLI not installed")
def test_mvn_deploy_creates_package(
    maven_repo_api_client,
    maven_package_api_client,
    maven_repo_factory,
    maven_distribution_factory,
    tmp_path,
    pulp_settings,
):
    """Verify that `mvn deploy` creates a MavenPackage for the deployed project."""
    repo = maven_repo_factory()
    maven_distribution_factory(repository=repo.pulp_href)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.check_output(
            ["cp", "-r", f"{current_dir}/../../assets/simple-project", f"{tmp_path}/simple-project"]
        )
        if pulp_settings.DOMAIN_ENABLED:
            escaped_repo_name = rf"default\/{repo.name}"
        else:
            escaped_repo_name = repo.name
        subprocess.check_output(
            [
                "sed",
                "-i",
                f"s/maven-snapshots/{escaped_repo_name}/g",
                f"{tmp_path}/simple-project/pom.xml",
            ]
        )
        subprocess.run(
            ["mvn", "deploy"],
            cwd=f"{tmp_path}/simple-project",
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as e:
        msg = e.stdout.decode() + e.stderr.decode()
        pytest.fail(msg)

    packages = maven_package_api_client.list(
        group_id="org.sonatype.nexus.examples",
        artifact_id="simple-project",
    )
    assert packages.count >= 1

    pkg = packages.results[0]
    assert pkg.name == "simple-project"
    assert pkg.packaging == "jar"
    assert pkg.version == "1.0.0-SNAPSHOT"


@pytest.mark.parallel
def test_partial_removal_keeps_package(
    maven_artifact_api_client,
    maven_package_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    random_artifact_factory,
    pom_file_factory,
    monitor_task,
):
    """Remove .jar but keep .pom — package stays. Then remove .pom — package removed."""
    repo = maven_repo_factory()
    group_path = "com/example/partial/partial-lib/1.0.0"

    jar_artifact = random_artifact_factory(size=64)
    jar = maven_artifact_api_client.upload(
        artifact=jar_artifact.pulp_href,
        relative_path=f"{group_path}/partial-lib-1.0.0.jar",
    )

    pom_path = pom_file_factory(
        group_id="com.example.partial",
        artifact_id="partial-lib",
        version="1.0.0",
    )
    pom = maven_artifact_api_client.upload(
        file=str(pom_path),
        relative_path=f"{group_path}/partial-lib-1.0.0.pom",
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"add_content_units": [jar.pulp_href, pom.pulp_href]},
        ).task
    )

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"remove_content_units": [jar.pulp_href]},
        ).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    packages = maven_package_api_client.list(repository_version=repo.latest_version_href)
    assert packages.count == 1

    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href,
            {"remove_content_units": [pom.pulp_href]},
        ).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    packages = maven_package_api_client.list(repository_version=repo.latest_version_href)
    assert packages.count == 0


def test_pullthrough_pom_no_package_until_modify(
    maven_artifact_api_client,
    maven_distribution_factory,
    maven_remote_factory,
    maven_repo_factory,
    maven_package_api_client,
    maven_repo_api_client,
    distribution_base_url,
    monitor_task,
):
    """Pull-through skips _ensure_packages; package appears only after a modify() adds new content.

    Pull-through caching deliberately skips package creation and metadata generation
    for performance. Verify that: (1) no package exists after pullthrough, and
    (2) adding new content for the same GAV triggers _ensure_packages and creates the package.
    """
    remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")
    repository = maven_repo_factory(remote=remote.pulp_href)
    distribution = maven_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )

    pom_path = "academy/alex/custommatcher/1.0/custommatcher-1.0.pom"
    pulp_url = urljoin(distribution_base_url(distribution.base_url), pom_path)

    downloaded = download_file(pulp_url)
    assert downloaded.response_obj.status == 200

    for _ in range(30):
        repository = maven_repo_api_client.read(repository.pulp_href)
        if not repository.latest_version_href.endswith("/versions/0/"):
            break
        time.sleep(1)

    packages = maven_package_api_client.list(
        repository_version=repository.latest_version_href,
    )
    assert packages.count == 0, "Pull-through should not create MavenPackage"

    # Now pull another artifact for the same GAV — a .jar.sha1.
    # This triggers a new version via add_cached_content, but pull-through still
    # skips _ensure_packages. Upload a new artifact via modify() to trigger it.
    jar_sha1_path = "academy/alex/custommatcher/1.0/custommatcher-1.0.jar.sha1"
    jar_sha1_url = urljoin(distribution_base_url(distribution.base_url), jar_sha1_path)
    download_file(jar_sha1_url)

    for _ in range(30):
        repo_now = maven_repo_api_client.read(repository.pulp_href)
        if repo_now.latest_version_href != repository.latest_version_href:
            repository = repo_now
            break
        time.sleep(1)

    # Trigger _ensure_packages by adding the cached artifacts to a fresh repo
    second_repo = maven_repo_factory()
    artifacts = maven_artifact_api_client.list(
        repository_version=repository.latest_version_href,
    )
    assert artifacts.count > 0
    hrefs = [a.pulp_href for a in artifacts.results]
    monitor_task(
        maven_repo_api_client.modify(
            second_repo.pulp_href, {"add_content_units": hrefs}
        ).task
    )

    second_repo = maven_repo_api_client.read(second_repo.pulp_href)
    packages = maven_package_api_client.list(
        repository_version=second_repo.latest_version_href,
        group_id="academy.alex",
        artifact_id="custommatcher",
    )
    assert packages.count >= 1
    assert packages.results[0].version == "1.0"


@pytest.mark.parallel
def test_rich_metadata_fields(
    maven_artifact_api_client,
    maven_package_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
):
    """Deploy a POM with licenses, dependencies, and SCM and verify all fields are captured."""
    repo = maven_repo_factory()

    pom_path = pom_file_factory(
        group_id="com.example.rich",
        artifact_id="rich-lib",
        version="1.0.0",
        name="Rich Library",
        description="A library with rich metadata",
        packaging="jar",
        url="https://example.com/rich-lib",
        licenses=[
            {"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
            {"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
        ],
        dependencies=[
            {
                "group_id": "org.slf4j",
                "artifact_id": "slf4j-api",
                "version": "1.7.36",
            },
            {
                "group_id": "junit",
                "artifact_id": "junit",
                "version": "4.13.2",
                "scope": "test",
            },
        ],
        scm_url="https://github.com/example/rich-lib",
    )

    content = maven_artifact_api_client.upload(
        file=str(pom_path),
        relative_path="com/example/rich/rich-lib/1.0.0/rich-lib-1.0.0.pom",
    )
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": [content.pulp_href]}
        ).task
    )

    repo = maven_repo_api_client.read(repo.pulp_href)
    packages = maven_package_api_client.list(repository_version=repo.latest_version_href)
    assert packages.count == 1

    pkg = packages.results[0]
    assert pkg.name == "Rich Library"
    assert pkg.description == "A library with rich metadata"
    assert pkg.packaging == "jar"
    assert pkg.url == "https://example.com/rich-lib"
    assert pkg.scm_url == "https://github.com/example/rich-lib"

    assert len(pkg.licenses) == 2
    assert pkg.licenses[0]["name"] == "Apache-2.0"
    assert pkg.licenses[0]["url"] == "https://www.apache.org/licenses/LICENSE-2.0"
    assert pkg.licenses[1]["name"] == "MIT"

    assert len(pkg.dependencies) == 2
    dep_ids = {d["artifact_id"] for d in pkg.dependencies}
    assert "slf4j-api" in dep_ids
    assert "junit" in dep_ids

    test_dep = next(d for d in pkg.dependencies if d["artifact_id"] == "junit")
    assert test_dep["scope"] == "test"


@pytest.mark.parallel
def test_package_api_read_only(maven_package_api_client):
    """Verify that the package endpoint is read-only (no create/update/delete methods)."""
    assert not hasattr(maven_package_api_client, "create")
    assert not hasattr(maven_package_api_client, "update")
    assert not hasattr(maven_package_api_client, "delete")
