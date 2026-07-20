from rest_framework import serializers


class MavenPackageMetadataSerializer(serializers.Serializer):
    """A Serializer for a Maven artifact's metadata."""

    last_serial = serializers.IntegerField(help_text="Cache value for serial tracking")
    info = serializers.JSONField(
        help_text="Core metadata of the artifact (group_id, artifact_id, version)"
    )
    releases = serializers.JSONField(help_text="All releases of the artifact grouped by version")
    urls = serializers.JSONField(help_text="Download URLs for the latest (or requested) version")
