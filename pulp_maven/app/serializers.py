import json
from gettext import gettext as _

from django.db import DatabaseError
from rest_framework import serializers

from pulpcore.plugin import serializers as platform
from pulpcore.plugin.models import Artifact
from pulpcore.plugin.util import get_domain_pk

from . import models


class MavenRepositorySerializer(platform.RepositorySerializer):
    """
    Serializer for Maven Repositories.
    """

    class Meta:
        fields = platform.RepositorySerializer.Meta.fields
        model = models.MavenRepository


class MavenArtifactSerializer(platform.SingleArtifactContentUploadSerializer):
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

    def create(self, validated_data):
        group_id, artifact_id, version, filename = (
            models.MavenArtifact.group_artifact_version_filename(validated_data["relative_path"])
        )
        validated_data["group_id"] = group_id
        validated_data["artifact_id"] = artifact_id
        validated_data["version"] = version
        validated_data["filename"] = filename
        return super().create(validated_data)

    class Meta:
        fields = platform.SingleArtifactContentUploadSerializer.Meta.fields + (
            "group_id",
            "artifact_id",
            "version",
            "filename",
        )
        model = models.MavenArtifact


class MavenArtifactUploadSerializer(MavenArtifactSerializer):
    """
    Serializer for synchronous Maven Artifact uploads.
    """

    def __init__(self, *args, **kwargs):
        if (
            "data" in kwargs
            and "pulp_labels" in kwargs["data"]
            and isinstance(kwargs["data"]["pulp_labels"], str)
        ):
            try:
                data = kwargs["data"].copy()
                data["pulp_labels"] = json.loads(data["pulp_labels"])
                kwargs["data"] = data
            except (json.JSONDecodeError, AttributeError):
                pass
        super().__init__(*args, **kwargs)

    def validate(self, data):
        data = super().validate(data)
        if "file" in data:
            file = data.pop("file")
            try:
                artifact = Artifact.objects.get(
                    sha256=file.hashers["sha256"].hexdigest(), pulp_domain=get_domain_pk()
                )
                artifact.touch()
            except (Artifact.DoesNotExist, DatabaseError):
                artifact = Artifact.init_and_validate(file)
                artifact.save()
            data["artifact"] = artifact
        return data

    class Meta(MavenArtifactSerializer.Meta):
        ref_name = "MavenArtifactUploadSerializer"


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
