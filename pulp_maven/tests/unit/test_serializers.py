import uuid
from unittest.mock import patch

from pulp_maven.app.serializers import MavenRemoteSerializer


def test_create_maven_remote_with_invalid_parameter():
    serializer = MavenRemoteSerializer(
        data={
            "name": str(uuid.uuid4()),
            "url": "http://example.com",
            "foo": "bar",
        }
    )

    with (
        patch("pulpcore.app.serializers.base.get_domain", return_value=uuid.uuid4()),
        patch("rest_framework.validators.qs_exists", return_value=False),
    ):
        assert serializer.is_valid() is False
    assert serializer.errors["foo"][0].title() == "Unexpected Field"


def test_create_maven_remote_without_url():
    serializer = MavenRemoteSerializer(data={"name": str(uuid.uuid4())})

    with (
        patch("pulpcore.app.serializers.base.get_domain", return_value=uuid.uuid4()),
        patch("rest_framework.validators.qs_exists", return_value=False),
    ):
        assert serializer.is_valid() is False
    assert serializer.errors["url"][0].title() == "This Field Is Required."
