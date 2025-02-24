from gettext import gettext as _
from logging import getLogger
from os import path
import re

from django.db import models

from pulpcore.plugin.models import Content, Remote, Repository, Distribution
from pulpcore.plugin.util import get_domain_pk

logger = getLogger(__name__)


class MavenContentMixin:
    @staticmethod
    def group_artifact_version_filename(relative_path):
        """
        Converts a relative path into a tuple of group_id, artifact_id, and version.

        Args:
            relative_path (str): Relative path for the artifact in the repository.

        Returns:
            Tuple (group_id, artifact_id, version, filename)

        """
        sub_path, filename = path.split(relative_path)
        sub_path, version = path.split(sub_path)
        pattern = re.compile(r"\d+(\.\d+)?(\.\d+)?([.-][a-zA-Z0-9]+)*")
        if pattern.match(version) is None:
            artifact_id = version
            version = None
            group_id = sub_path.replace("/", ".")
        else:
            sub_path, artifact_id = path.split(sub_path)
            group_id = sub_path.replace("/", ".")

        return group_id, artifact_id, version, filename


class MavenArtifact(MavenContentMixin, Content):
    """
    The Maven artifact content type.

    This content type represents a single file in a Maven repository.
    """

    TYPE = "artifact"

    _pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.PROTECT)
    group_id = models.CharField(max_length=255, null=False)
    artifact_id = models.CharField(max_length=255, null=False)
    version = models.CharField(max_length=255, null=False)
    filename = models.CharField(max_length=255, null=False)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        unique_together = ("group_id", "artifact_id", "version", "filename", "_pulp_domain")

    @staticmethod
    def init_from_artifact_and_relative_path(artifact, relative_path):
        """
        Returns an instance of MavenArtifact for this artifact.

        Args:
            artifact (:class:`~pulpcore.plugin.models.Artifact`): An instance of an Artifact
            relative_path (str): Relative path for the artifact in the Project

        """
        if path.isabs(relative_path):
            raise ValueError(_("Relative path can't start with '/'."))

        group_id, artifact_id, version, f_name = MavenArtifact.group_artifact_version_filename(
            relative_path
        )

        return MavenArtifact(
            group_id=group_id, artifact_id=artifact_id, version=version, filename=f_name
        )


class MavenMetadata(MavenContentMixin, Content):
    """
    The Maven Metadata content type.

    This content type represents a pom file or a pom.<checksum_type> file in a Maven repository.
    """

    TYPE = "metadata"

    _pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.PROTECT)
    group_id = models.CharField(max_length=255, null=False)
    artifact_id = models.CharField(max_length=255, null=False)
    version = models.CharField(max_length=255, null=True)
    filename = models.CharField(max_length=255, null=False)
    sha256 = models.CharField(max_length=64, null=False, unique=True, db_index=True)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        unique_together = (
            "group_id",
            "artifact_id",
            "version",
            "filename",
            "sha256",
            "_pulp_domain",
        )

    @staticmethod
    def init_from_artifact_and_relative_path(artifact, relative_path):
        """
        Returns an instance of MavenMetadata for this artifact.

        Args:
            artifact (:class:`~pulpcore.plugin.models.Artifact`): An instance of an Artifact
            relative_path (str): Relative path for the artifact in the Project

        """
        if path.isabs(relative_path):
            raise ValueError(_("Relative path can't start with '/'."))

        group_id, artifact_id, version, f_name = MavenMetadata.group_artifact_version_filename(
            relative_path
        )

        return MavenMetadata(
            group_id=group_id,
            artifact_id=artifact_id,
            version=version,
            filename=f_name,
            sha256=artifact.sha256,
        )


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
        pattern = r"\.(xml|xml\.sha1|xml\.md5|xml\.sha224|xml\.sha256|xml\.sha384|xml\.sha512)$"
        if re.search(pattern, relative_path):
            return MavenMetadata
        return MavenArtifact

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"


class MavenDistribution(Distribution):
    """
    Distribution for 'maven' content.
    """

    TYPE = "maven"

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"


class MavenRepository(Repository):
    """
    Repository for "maven" content.
    """

    TYPE = "maven"
    CONTENT_TYPES = [MavenArtifact, MavenMetadata]
    REMOTE_TYPES = [MavenRemote]

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
