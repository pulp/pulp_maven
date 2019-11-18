from gettext import gettext as _

from rest_framework import serializers

from pulpcore.plugin import serializers as platform

from . import models


class MavenRepositorySerializer(platform.RepositorySerializer):
    """
    Serializer for Maven Repositories.
    """

    class Meta:
        fields = platform.RepositorySerializer.Meta.fields
        model = models.MavenRepository


class MavenArtifactSerializer(platform.SingleArtifactContentSerializer):
    """
    A Serializer for MavenArtifact.
    """

    group_id = serializers.CharField(
        help_text=_("Group Id of the artifact's package."), read_only=True
    )
    artifact_id = serializers.CharField(
        help_text=_("Artifact Id of the artifact's package."), read_only=True
    )
    version = serializers.CharField(
        help_text=_("Version of the artifact's package."), read_only=True
    )
    filename = serializers.CharField(help_text=_("Filename of the artifact."), read_only=True)

    class Meta:
        fields = platform.SingleArtifactContentSerializer.Meta.fields + (
            "group_id",
            "artifact_id",
            "version",
            "filename",
        )
        model = models.MavenArtifact


class MavenRemoteSerializer(platform.RemoteSerializer):
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
        fields = platform.RemoteSerializer.Meta.fields
        model = models.MavenRemote


class MavenDistributionSerializer(platform.BaseDistributionSerializer):
    """
    Serializer for Maven Distributions.
    """

    remote = platform.DetailRelatedField(
        required=False,
        help_text=_("Remote that can be used to fetch content when using pull-through caching."),
        queryset=models.MavenRemote.objects.all(),
        allow_null=True,
    )

    class Meta:
        fields = platform.BaseDistributionSerializer.Meta.fields + ("remote",)
        model = models.MavenDistribution
