"""Tests that CRUD maven remotes."""

import json
import uuid

import pytest

import django

from pulpcore.client.pulp_maven.exceptions import ApiException

django.setup()
from pulp_maven.app.serializers import MavenRemoteSerializer  # noqa


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
def test_create_maven_remote_with_invalid_parameter():
    unexpected_field_remote_data = {
        "name": str(uuid.uuid4()),
        "url": "http://example.com",
        "foo": "bar",
    }

    maven_remote_serializer = MavenRemoteSerializer(data=unexpected_field_remote_data)

    assert maven_remote_serializer.is_valid() is False
    assert maven_remote_serializer.errors["foo"][0].title() == "Unexpected Field"


@pytest.mark.parallel
def test_create_maven_remote_without_url(maven_remote_api_client, gen_object_with_cleanup):
    maven_remote_serializer = MavenRemoteSerializer(data={"name": str(uuid.uuid4())})

    assert maven_remote_serializer.is_valid() is False
    assert maven_remote_serializer.errors["url"][0].title() == "This Field Is Required."


@pytest.mark.parallel
def test_default_remote_policy_immediate(maven_remote_api_client, gen_object_with_cleanup):
    remote_data = {"name": str(uuid.uuid4()), "url": "http://example.com"}
    remote = gen_object_with_cleanup(maven_remote_api_client, remote_data)
    assert remote.policy == "immediate"
