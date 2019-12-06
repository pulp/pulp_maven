from gettext import gettext as _
from logging import getLogger
from os import path

from django.db import models

from pulpcore.plugin.models import Content, Remote, Repository, BaseDistribution

logger = getLogger(__name__)


class MavenArtifact(Content):
    """
    The Maven artifact content type.

    This content type represents a single file in a Maven repository.
    """

    TYPE = "artifact"

    group_id = models.CharField(max_length=255, null=False)
    artifact_id = models.CharField(max_length=255, null=False)
    version = models.CharField(max_length=255, null=False)
    filename = models.CharField(max_length=255, null=False)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        unique_together = ("group_id", "artifact_id", "version", "filename")

    @staticmethod
    def init_from_artifact_and_relative_path(artifact, relative_path):
        """
        Returns an instance of Project for this artifact.

        Args:
            artifact (:class:`~pulpcore.plugin.models.Artifact`): An instance of an Artifact
            relative_path (str): Relative path for the artifact in the Project

        """
        if path.isabs(relative_path):
            raise ValueError(_("Relative path can't start with '/'."))

        group_id, artifact_id, versn, f_name = MavenArtifact._get_group_artifact_version_filename(
            relative_path
        )

        return MavenArtifact(
            group_id=group_id, artifact_id=artifact_id, version=versn, filename=f_name
        )

    @staticmethod
    def _get_group_artifact_version_filename(relative_path):
        """
        Converts a relative path into a tuple of group_id, artifact_id, and version.

        Args:
            relative_path (str): Relative path for the artifact in the repository.

        Returns:
            Tuple (group_id, artifact_id, version, filename)

        """
        sub_path, filename = path.split(relative_path)
        sub_path, version = path.split(sub_path)
        sub_path, artifact_id = path.split(sub_path)
        group_id = sub_path.replace("/", ".")

        return group_id, artifact_id, version, filename


class MavenRepository(Repository):
    """
    Repository for "maven" content.
    """

    TYPE = "maven"
    CONTENT_TYPES = [MavenArtifact]

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"


class MavenRemote(Remote):
    """
    A Remote for MavenArtifact.

    Define any additional fields for your new importer if needed.
    """

    TYPE = "maven"

    @staticmethod
    def get_remote_artifact_content_type(relative_path=None):
        """
        Returns content type that is found at the relative_path.
        """
        return MavenArtifact

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"


class MavenDistribution(BaseDistribution):
    """
    Distribution for 'maven' content.
    """

    TYPE = "maven"

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
