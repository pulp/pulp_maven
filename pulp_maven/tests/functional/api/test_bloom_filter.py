"""Functional tests for repository Bloom filters."""

import uuid
from urllib.parse import urljoin

import aiohttp
import pytest
from redis.exceptions import RedisError

from pulp_maven.app.bloom import (
    BLOOM_FILTER_KEY_PREFIX,
    get_redis_connection,
    grown_bloom_filter_capacity,
)
from pulp_maven.tests.functional.utils import download_file

BLOOM_LABEL = "pulp_maven.bloom"
FALSE_POSITIVE_RATE = 0.0001


@pytest.fixture(autouse=True)
def require_redis(redis_status):
    if not redis_status:
        pytest.skip("Redis is disabled in this test scenario")

    try:
        command_info = get_redis_connection().execute_command("COMMAND", "INFO", "BF.RESERVE")
    except (RedisError, TypeError):
        pytest.skip("Redis/Valkey Bloom support is not installed")

    if not command_info or isinstance(command_info, (list, tuple)) and not command_info[0]:
        pytest.skip("Redis/Valkey Bloom support is not installed")


def _upload_artifact(maven_artifact_api_client, random_artifact_factory):
    token = uuid.uuid4().hex
    artifact_id = f"bloom-{token}"
    relative_path = f"com/example/{token}/{artifact_id}/1.0/{artifact_id}-1.0.jar"
    artifact = random_artifact_factory(size=64)
    content = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=relative_path,
    )
    return content, relative_path


def _add_content(maven_repo_api_client, monitor_task, repository, content):
    response = maven_repo_api_client.modify(
        repository.pulp_href,
        {"add_content_units": [content.pulp_href]},
    )
    monitor_task(response.task)


def _update_labels(maven_repo_api_client, monitor_task, repository, labels):
    response = maven_repo_api_client.partial_update(
        repository.pulp_href,
        {"pulp_labels": labels},
    )
    monitor_task(response.task)
    return maven_repo_api_client.read(repository.pulp_href)


def _filter_contains(repository, path):
    return bool(get_redis_connection().execute_command("BF.EXISTS", _filter_key(repository), path))


def _filter_info(repository):
    return get_redis_connection().bf().info(_filter_key(repository))


def _stored_filter_config(repository):
    config = get_redis_connection().get(f"{_filter_key(repository)}:config")
    return config.decode() if isinstance(config, bytes) else config


def _filter_key(repository):
    repository_pk = repository.pulp_href.rstrip("/").rsplit("/", 1)[-1]
    return f"{BLOOM_FILTER_KEY_PREFIX}:{repository_pk}"


def _content_artifact_count(
    maven_bindings,
    maven_artifact_api_client,
    maven_metadata_api_client,
    repository_version,
):
    artifact_count = maven_artifact_api_client.list(
        repository_version=repository_version,
        limit=1,
    ).count
    metadata_count = maven_metadata_api_client.list(
        repository_version=repository_version,
        limit=1,
    ).count
    index_count = maven_bindings.ContentMavenIndexPageApi.list(
        repository_version=repository_version,
        limit=1,
    ).count
    return artifact_count + metadata_count + index_count


@pytest.mark.parallel
def test_filter_updates_for_each_content_addition(
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    monitor_task,
    random_artifact_factory,
):
    """Each repository version adds its new paths to the existing filter."""
    repository = maven_repo_factory(pulp_labels={BLOOM_LABEL: f"1000,{FALSE_POSITIVE_RATE}"})
    paths = []
    previous_inserted_count = 0

    for _ in range(2):
        content, relative_path = _upload_artifact(
            maven_artifact_api_client,
            random_artifact_factory,
        )
        _add_content(maven_repo_api_client, monitor_task, repository, content)
        paths.append(relative_path)

        repository = maven_repo_api_client.read(repository.pulp_href)
        info = _filter_info(repository)

        assert all(_filter_contains(repository, path) for path in paths)
        assert info.insertedNum > previous_inserted_count
        previous_inserted_count = info.insertedNum


@pytest.mark.parallel
def test_changing_config_rebuilds_filter(
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    monitor_task,
    random_artifact_factory,
):
    """Changing either filter setting rebuilds it without losing existing paths."""
    repository = maven_repo_factory(pulp_labels={BLOOM_LABEL: "1000,0.01"})
    content, relative_path = _upload_artifact(
        maven_artifact_api_client,
        random_artifact_factory,
    )
    _add_content(maven_repo_api_client, monitor_task, repository, content)

    repository = maven_repo_api_client.read(repository.pulp_href)
    old_filter_config = _stored_filter_config(repository)
    labels = dict(repository.pulp_labels)
    labels[BLOOM_LABEL] = f"2000,{FALSE_POSITIVE_RATE}"
    repository = _update_labels(
        maven_repo_api_client,
        monitor_task,
        repository,
        labels,
    )

    assert _stored_filter_config(repository) != old_filter_config
    assert _stored_filter_config(repository) == f"2000,{FALSE_POSITIVE_RATE}"
    assert _filter_info(repository).capacity == 2000
    assert _filter_contains(repository, relative_path)


@pytest.mark.parallel
def test_filter_rebuilds_when_content_exceeds_its_size(
    maven_artifact_api_client,
    maven_bindings,
    maven_metadata_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    monitor_task,
    random_artifact_factory,
):
    """Crossing the configured item limit grows and rebuilds the filter."""
    repository = maven_repo_factory()
    first_content, first_path = _upload_artifact(
        maven_artifact_api_client,
        random_artifact_factory,
    )
    _add_content(maven_repo_api_client, monitor_task, repository, first_content)
    repository = maven_repo_api_client.read(repository.pulp_href)

    initial_count = _content_artifact_count(
        maven_bindings,
        maven_artifact_api_client,
        maven_metadata_api_client,
        repository.latest_version_href,
    )
    initial_size = initial_count + 1
    repository = _update_labels(
        maven_repo_api_client,
        monitor_task,
        repository,
        {BLOOM_LABEL: f"{initial_size},{FALSE_POSITIVE_RATE}"},
    )
    assert _filter_info(repository).capacity == initial_size

    second_content, second_path = _upload_artifact(
        maven_artifact_api_client,
        random_artifact_factory,
    )
    _add_content(maven_repo_api_client, monitor_task, repository, second_content)
    repository = maven_repo_api_client.read(repository.pulp_href)

    new_count = _content_artifact_count(
        maven_bindings,
        maven_artifact_api_client,
        maven_metadata_api_client,
        repository.latest_version_href,
    )
    assert new_count > initial_size
    grown_capacity = grown_bloom_filter_capacity(new_count)
    assert _filter_info(repository).capacity == grown_capacity
    assert repository.pulp_labels[BLOOM_LABEL] == f"{grown_capacity},{FALSE_POSITIVE_RATE}"
    assert _filter_contains(repository, first_path)
    assert _filter_contains(repository, second_path)


@pytest.mark.parallel
def test_filter_allows_distribution_downloads(
    distribution_base_url,
    maven_artifact_api_client,
    maven_distribution_factory,
    maven_repo_api_client,
    maven_repo_factory,
    monitor_task,
    random_artifact_factory,
):
    """A repository distribution serves present paths and rejects absent ones early."""
    repository = maven_repo_factory(pulp_labels={BLOOM_LABEL: f"1000,{FALSE_POSITIVE_RATE}"})
    distribution = maven_distribution_factory(repository=repository.pulp_href)
    content, relative_path = _upload_artifact(
        maven_artifact_api_client,
        random_artifact_factory,
    )
    _add_content(maven_repo_api_client, monitor_task, repository, content)

    base_url = distribution_base_url(distribution.base_url)
    downloaded = download_file(urljoin(base_url, relative_path))
    assert downloaded.response_obj.status == 200

    missing_path = f"com/example/{uuid.uuid4().hex}/missing/1.0/missing-1.0.jar"
    with pytest.raises(aiohttp.ClientResponseError) as exc_info:
        download_file(urljoin(base_url, missing_path))

    assert exc_info.value.status == 404
    assert exc_info.value.headers["X-Pulp-Bloom-Filtered"] == "True"
