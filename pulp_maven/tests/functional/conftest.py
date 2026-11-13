import uuid

import pytest

from pulpcore.client.pulp_maven import (
    ApiClient,
    ContentArtifactApi,
    ContentMetadataApi,
    ContentPackageApi,
    DistributionsMavenApi,
    RemotesMavenApi,
    RepositoriesMavenApi,
)
from pulpcore.tests.functional.utils import BindingsNamespace


@pytest.fixture(scope="session")
def maven_client(_api_client_set, bindings_cfg):
    api_client = ApiClient(bindings_cfg)
    _api_client_set.add(api_client)
    yield api_client
    _api_client_set.remove(api_client)


@pytest.fixture(scope="session")
def maven_bindings(_api_client_set, bindings_cfg):
    """A namespace providing preconfigured pulp_maven API clients."""
    from pulpcore.client import pulp_maven as maven_bindings_module

    api_client = maven_bindings_module.ApiClient(bindings_cfg)
    _api_client_set.add(api_client)
    yield BindingsNamespace(maven_bindings_module, api_client)
    _api_client_set.remove(api_client)


@pytest.fixture(scope="session")
def maven_artifact_api_client(maven_client):
    return ContentArtifactApi(maven_client)


@pytest.fixture(scope="session")
def maven_metadata_api_client(maven_client):
    return ContentMetadataApi(maven_client)


@pytest.fixture(scope="session")
def maven_package_api_client(maven_client):
    return ContentPackageApi(maven_client)


@pytest.fixture(scope="session")
def maven_distro_api_client(maven_client):
    return DistributionsMavenApi(maven_client)


@pytest.fixture(scope="session")
def maven_repo_api_client(maven_client):
    return RepositoriesMavenApi(maven_client)


@pytest.fixture(scope="session")
def maven_remote_api_client(maven_client):
    return RemotesMavenApi(maven_client)


@pytest.fixture
def maven_distribution_factory(maven_distro_api_client, gen_object_with_cleanup):
    def _maven_distribution_factory(**kwargs):
        data = {"base_path": str(uuid.uuid4()), "name": str(uuid.uuid4())}
        data.update(kwargs)
        return gen_object_with_cleanup(maven_distro_api_client, data)

    return _maven_distribution_factory


@pytest.fixture
def maven_repo_factory(maven_repo_api_client, gen_object_with_cleanup):
    """A factory to generate a Maven Repository with auto-deletion after the test run."""

    def _maven_repo_factory(**kwargs):
        kwargs.setdefault("name", str(uuid.uuid4()))
        return gen_object_with_cleanup(maven_repo_api_client, kwargs)

    yield _maven_repo_factory


@pytest.fixture
def maven_remote_factory(maven_remote_api_client, gen_object_with_cleanup):
    """A factory to generate a Maven Remote with auto-deletion after the test run."""

    def _maven_remote_factory(**kwargs):
        kwargs.setdefault("name", str(uuid.uuid4()))
        return gen_object_with_cleanup(maven_remote_api_client, kwargs)

    yield _maven_remote_factory


@pytest.fixture
def stale_index_page_repo(
    maven_bindings,
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Factory for a repository whose index pages list a directory that has no content.

    A source repository gets content under ``com/{uid}/kept/`` and ``com/{uid}/stale/``.
    Its pages for ``""``, ``com/`` and ``com/{uid}/`` are then added on their own to a new
    repository. Adding only index pages does not regenerate them, so the new repository
    lists ``stale/`` (and ``kept/``) without any content or child page behind the entry:
    the state left behind when content and its child pages are removed while the parent
    pages are not regenerated.

    Returns:
        (repo, base_url, uid, kept_hrefs), where ``kept_hrefs`` are the content units
        under ``com/{uid}/kept/`` in the source repository.
    """

    def _factory():
        uid = uuid.uuid4().hex[:8]
        source = maven_repo_factory()
        content_hrefs = {}
        for name in ("kept", "stale"):
            artifact = random_artifact_factory(size=64)
            content_hrefs[name] = maven_artifact_api_client.upload(
                artifact=artifact.pulp_href,
                relative_path=f"com/{uid}/{name}/1.0/{name}-1.0.jar",
            ).pulp_href
        monitor_task(
            maven_repo_api_client.modify(
                source.pulp_href, {"add_content_units": list(content_hrefs.values())}
            ).task
        )
        source = maven_repo_api_client.read(source.pulp_href)
        pages = maven_bindings.ContentMavenIndexPageApi.list(
            repository_version=source.latest_version_href, limit=100
        ).results
        parent_page_hrefs = [p.pulp_href for p in pages if p.path in ("", "com/", f"com/{uid}/")]
        assert len(parent_page_hrefs) == 3

        repo = maven_repo_factory()
        distro = maven_distribution_factory(repository=repo.pulp_href)
        monitor_task(
            maven_repo_api_client.modify(
                repo.pulp_href, {"add_content_units": parent_page_hrefs}
            ).task
        )
        repo = maven_repo_api_client.read(repo.pulp_href)
        return repo, distribution_base_url(distro.base_url), uid, [content_hrefs["kept"]]

    return _factory


@pytest.fixture
def pom_file_factory(tmp_path):
    """Create POM XML files with specified metadata.

    Each file includes a unique XML comment so parallel pytest-xdist workers
    cannot hit ``core_artifact_sha256_pulp_domain_id_*`` when uploading the
    same GAV (see pulp/pulp_maven#459).
    """
    _counter = 0

    def _pom_file_factory(
        group_id="com.example",
        artifact_id="test-lib",
        version="1.0.0",
        name=None,
        description=None,
        packaging="jar",
        url=None,
        licenses=None,
        dependencies=None,
        scm_url=None,
    ):
        nonlocal _counter
        _counter += 1
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<project xmlns="http://maven.apache.org/POM/4.0.0">',
            "  <modelVersion>4.0.0</modelVersion>",
            f"  <groupId>{group_id}</groupId>",
            f"  <artifactId>{artifact_id}</artifactId>",
            f"  <version>{version}</version>",
        ]
        if packaging:
            lines.append(f"  <packaging>{packaging}</packaging>")
        if name:
            lines.append(f"  <name>{name}</name>")
        if description:
            lines.append(f"  <description>{description}</description>")
        if url:
            lines.append(f"  <url>{url}</url>")
        if licenses:
            lines.append("  <licenses>")
            for lic in licenses:
                lines.append("    <license>")
                lines.append(f"      <name>{lic['name']}</name>")
                if "url" in lic:
                    lines.append(f"      <url>{lic['url']}</url>")
                lines.append("    </license>")
            lines.append("  </licenses>")
        if dependencies:
            lines.append("  <dependencies>")
            for dep in dependencies:
                lines.append("    <dependency>")
                lines.append(f"      <groupId>{dep['group_id']}</groupId>")
                lines.append(f"      <artifactId>{dep['artifact_id']}</artifactId>")
                if "version" in dep:
                    lines.append(f"      <version>{dep['version']}</version>")
                if "scope" in dep:
                    lines.append(f"      <scope>{dep['scope']}</scope>")
                lines.append("    </dependency>")
            lines.append("  </dependencies>")
        if scm_url:
            lines.append("  <scm>")
            lines.append(f"    <url>{scm_url}</url>")
            lines.append("  </scm>")
        lines.append(f"  <!-- pulp-test-salt: {uuid.uuid4().hex} -->")
        lines.append("</project>")

        filename = f"{artifact_id}-{version}.pom"
        pom_path = tmp_path / f"pom_{_counter}" / filename
        pom_path.parent.mkdir(parents=True, exist_ok=True)
        pom_path.write_text("\n".join(lines))
        return pom_path

    return _pom_file_factory
