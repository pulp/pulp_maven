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


def _remote_serializer(**extra):
    return MavenRemoteSerializer(
        data={"name": str(uuid.uuid4()), "url": "http://example.com", **extra}
    )


def _is_valid(serializer):
    with (
        patch("pulpcore.app.serializers.base.get_domain", return_value=uuid.uuid4()),
        patch("rest_framework.validators.qs_exists", return_value=False),
    ):
        return serializer.is_valid()


def test_exclude_group_ids_accepted_and_deduplicated():
    serializer = _remote_serializer(exclude_group_ids=["com.example", "org.a-b", "com.example"])
    assert _is_valid(serializer) is True
    assert serializer.validated_data["exclude_group_ids"] == ["com.example", "org.a-b"]


def test_exclude_group_ids_rejects_invalid_values():
    for bad in ("com/example", "com..example", ".com", "com.", "", "com example"):
        serializer = _remote_serializer(exclude_group_ids=[bad])
        assert _is_valid(serializer) is False, bad
        assert "exclude_group_ids" in serializer.errors
