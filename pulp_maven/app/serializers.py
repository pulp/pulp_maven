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


class MavenDistributionSerializer(platform.DistributionSerializer):
    """
    Serializer for Maven Distributions.
    """

    remote = platform.DetailRelatedField(
        required=False,
        help_text=_("Remote that can be used to fetch content when using pull-through caching."),
        queryset=models.MavenRemote.objects.all(),
        view_name="remotes-maven/maven-detail",
        allow_null=True,
    )

    class Meta:
        fields = platform.DistributionSerializer.Meta.fields + ("remote",)
        model = models.MavenDistribution


class RepositoryAddCachedContentSerializer(platform.ValidateFieldsMixin, serializers.Serializer):
    remote = platform.DetailRelatedField(
        required=False,
        view_name_pattern=r"remotes(-.*/.*)-detail",
        queryset=models.Remote.objects.all(),
        help_text=_(
            "A remote to use to identify content that was cached. This will override a "
            "remote set on repository."
        ),
    )

    def validate(self, data):
        data = super().validate(data)
        repository = None
        if "repository_pk" in self.context:
            repository = models.Repository.objects.get(pk=self.context["repository_pk"])
        remote = data.get("remote", None) or getattr(repository, "remote", None)

        if not remote:
            raise serializers.ValidationError(
                {"remote": _("This field is required since a remote is not set on the repository.")}
            )
        self.check_cross_domains({"repository": repository, "remote": remote})
        return data
