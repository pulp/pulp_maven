from gettext import gettext as _

from rest_framework import serializers

from pulpcore.plugin.serializers import (
    ContentChecksumSerializer,
    DetailRelatedField,
    DistributionSerializer,
    RemoteSerializer,
    RepositorySerializer,
    SingleArtifactContentUploadSerializer,
)

from . import models


class MavenRepositorySerializer(RepositorySerializer):
    """
    Serializer for Maven Repositories.
    """

    class Meta:
        fields = RepositorySerializer.Meta.fields
        model = models.MavenRepository


class MavenArtifactSerializer(SingleArtifactContentUploadSerializer, ContentChecksumSerializer):
    """
    A Serializer for MavenArtifact.
    """

    group_id = serializers.CharField(help_text=_("Group Id of the artifact's package."))
    artifact_id = serializers.CharField(help_text=_("Artifact Id of the artifact's package."))
    version = serializers.CharField(help_text=_("Version of the artifact's package."))
    filename = serializers.CharField(help_text=_("Filename of the artifact."))

    def deferred_validate(self, data):
        """Validate the FileContent data."""
        data = super().deferred_validate(data)
        data["relative_path"] = (
            f"{data['group_id'].replace('.', '/')}/{data['artifact_id']}/{data['version']}/"
            f"{data['filename']}"
        )
        return data

    def retrieve(self, validated_data):
        content = models.MavenArtifact.objects.filter(
            group_id=validated_data["group_id"],
            artifact_id=validated_data["artifact_id"],
            version=validated_data["version"],
            filename=validated_data["filename"],
        )
        return content.first()

    class Meta:
        fields = (
            SingleArtifactContentUploadSerializer.Meta.fields
            + ContentChecksumSerializer.Meta.fields
            + ("group_id", "artifact_id", "version", "filename")
        )
        # Remove relative_path
        fields = tuple(field for field in fields if field != "relative_path")
        model = models.MavenArtifact
        # Validation occurs in the task.
        validators = []


class MavenRemoteSerializer(RemoteSerializer):
    """
    A Serializer for MavenRemote.

    Add any new fields if defined on MavenRemote.
    Similar to the example above, in MavenArtifactSerializer.
    Additional validators can be added to the parent validators list

    For example::

    class Meta:
        validators = platform.RemoteSerializer.Meta.validators + [myValidator1, myValidator2]
    """

    class Meta:
        fields = RemoteSerializer.Meta.fields
        model = models.MavenRemote


class MavenDistributionSerializer(DistributionSerializer):
    """
    Serializer for Maven Distributions.
    """

    remote = DetailRelatedField(
        required=False,
        help_text=_("Remote that can be used to fetch content when using pull-through caching."),
        queryset=models.MavenRemote.objects.all(),
        view_name="remotes-maven/maven-detail",
        allow_null=True,
    )

    class Meta:
        fields = DistributionSerializer.Meta.fields + ("remote",)
        model = models.MavenDistribution
