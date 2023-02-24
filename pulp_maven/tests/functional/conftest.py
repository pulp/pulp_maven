import uuid

import pytest

from pulpcore.client.pulp_maven import (
    ApiClient,
    ContentArtifactApi,
    DistributionsMavenApi,
    RemotesMavenApi,
    RepositoriesMavenApi,
)


@pytest.fixture(scope="session")
def maven_client(_api_client_set, bindings_cfg):
    api_client = ApiClient(bindings_cfg)
    _api_client_set.add(api_client)
    yield api_client
    _api_client_set.remove(api_client)


@pytest.fixture(scope="session")
def maven_artifact_api_client(maven_client):
    return ContentArtifactApi(maven_client)


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
