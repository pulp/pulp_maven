"""Tests that CRUD maven remotes."""

import json
import uuid

import pytest

from pulpcore.client.pulp_maven.exceptions import ApiException


@pytest.mark.parallel
def test_remote_crud_workflow(maven_remote_api_client, gen_object_with_cleanup, monitor_task):
    remote_data = {"name": str(uuid.uuid4()), "url": "http://example.com"}
    remote = gen_object_with_cleanup(maven_remote_api_client, remote_data)
    assert remote.url == remote_data["url"]
    assert remote.name == remote_data["name"]

    with pytest.raises(ApiException) as exc:
        gen_object_with_cleanup(maven_remote_api_client, remote_data)
    assert exc.value.status == 400
    assert json.loads(exc.value.body) == {"name": ["This field must be unique."]}

    update_response = maven_remote_api_client.partial_update(
        remote.pulp_href, {"url": "https://example.com"}
    )
    task = monitor_task(update_response.task)
    assert task.created_resources == []

    remote = maven_remote_api_client.read(remote.pulp_href)
    assert remote.url == "https://example.com"

    all_new_remote_data = {"name": str(uuid.uuid4()), "url": "http://example.com"}
    update_response = maven_remote_api_client.update(remote.pulp_href, all_new_remote_data)
    task = monitor_task(update_response.task)
    assert task.created_resources == []

    remote = maven_remote_api_client.read(remote.pulp_href)
    assert remote.name == all_new_remote_data["name"]
    assert remote.url == all_new_remote_data["url"]


@pytest.mark.parallel
def test_create_maven_remote_with_invalid_parameter(
    maven_remote_api_client, gen_object_with_cleanup
):
    unexpected_field_remote_data = {
        "name": str(uuid.uuid4()),
        "url": "http://example.com",
        "foo": "bar",
    }

    with pytest.raises(ApiException) as exc:
        gen_object_with_cleanup(maven_remote_api_client, unexpected_field_remote_data)
    assert exc.value.status == 400
    assert json.loads(exc.value.body) == {"foo": ["Unexpected field"]}


@pytest.mark.parallel
def test_create_maven_remote_without_url(maven_remote_api_client, gen_object_with_cleanup):
    with pytest.raises(ApiException) as exc:
        gen_object_with_cleanup(maven_remote_api_client, {"name": str(uuid.uuid4())})
    assert exc.value.status == 400
    assert json.loads(exc.value.body) == {"url": ["This field is required."]}


@pytest.mark.parallel
def test_default_remote_policy_immediate(maven_remote_api_client, gen_object_with_cleanup):
    remote_data = {"name": str(uuid.uuid4()), "url": "http://example.com"}
    remote = gen_object_with_cleanup(maven_remote_api_client, remote_data)
    assert remote.policy == "immediate"
