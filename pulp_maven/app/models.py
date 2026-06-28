import re
from gettext import gettext as _
from logging import getLogger
from os import path

import defusedxml.ElementTree as ET
from django.db import models

from pulpcore.plugin.models import Content, ContentArtifact, Distribution, Remote, Repository
from pulpcore.plugin.repo_version_utils import remove_duplicates
from pulpcore.plugin.util import get_domain_pk

logger = getLogger(__name__)

_METADATA_PATTERN = re.compile(
    r"\.(xml|xml\.sha1|xml\.md5|xml\.sha224|xml\.sha256|xml\.sha384|xml\.sha512)$"
)


class MavenArtifact(Content):
    """
    The Maven content type.

    Represents any file in a Maven repository: JARs, POMs, signatures,
    maven-metadata.xml, and their checksum sidecars.
    """

    TYPE = "artifact"
    repo_key_fields = ("group_id", "artifact_id", "version", "filename")

    _pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.PROTECT)
    group_id = models.CharField(max_length=255, null=False)
    artifact_id = models.CharField(max_length=255, null=False)
    version = models.CharField(max_length=255, null=True)
    filename = models.CharField(max_length=255, null=False)
    sha256 = models.CharField(max_length=64, null=False)

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
    def group_artifact_version_filename(relative_path):
        """
        Parse a Maven repository relative path into (group_id, artifact_id, version, filename).

        The version is determined by a regex heuristic. If the directory above the filename
        looks like a version string (contains at least one digit), it is treated as the version.
        Otherwise, it is treated as the artifact_id and version is set to None.
        """
        sub_path, filename = path.split(relative_path)
        sub_path, version = path.split(sub_path)
        pattern = re.compile(r"(?=.*\d)[a-zA-Z0-9]+([.-][a-zA-Z0-9]+)*")
        if pattern.match(version) is None:
            artifact_id = version
            version = None
            group_id = sub_path.replace("/", ".")
        else:
            sub_path, artifact_id = path.split(sub_path)
            group_id = sub_path.replace("/", ".")

        return group_id, artifact_id, version, filename

    @staticmethod
    def is_metadata(relative_path):
        """Return True if the relative_path is for a maven-metadata.xml or its checksum."""
        return bool(_METADATA_PATTERN.search(relative_path))

    @staticmethod
    def init_from_artifact_and_relative_path(artifact, relative_path):
        """
        Return a MavenArtifact instance with fields populated from the artifact and path.

        For maven-metadata.xml files, groupId and artifactId are parsed from the XML content.
        For checksum sidecars (.xml.sha1, etc.), GAV is inherited from the parent metadata.
        For all other files, GAV is parsed from the relative_path.
        """
        if path.isabs(relative_path):
            raise ValueError(_("Relative path can't start with '/'."))

        _, _, _, f_name = MavenArtifact.group_artifact_version_filename(relative_path)

        if f_name == "maven-metadata.xml":
            with artifact.file.open("rb") as f:
                tree = ET.parse(f)
                root = tree.getroot()
                group_id = root.findtext("groupId", "")
                artifact_id = root.findtext("artifactId", "")
                version = root.findtext("version")
        elif MavenArtifact.is_metadata(relative_path):
            parent_path = relative_path.rsplit(".", 1)[0]
            parent_ca = ContentArtifact.objects.filter(
                relative_path=parent_path,
                content__pulp_domain=get_domain_pk(),
            ).first()
            if parent_ca:
                parent = parent_ca.content.cast()
                group_id = parent.group_id
                artifact_id = parent.artifact_id
                version = parent.version
            else:
                group_id, artifact_id, version, _ = MavenArtifact.group_artifact_version_filename(
                    relative_path
                )
        else:
            group_id, artifact_id, version, _ = MavenArtifact.group_artifact_version_filename(
                relative_path
            )

        return MavenArtifact(
            group_id=group_id,
            artifact_id=artifact_id,
            version=version,
            filename=f_name,
            sha256=artifact.sha256,
        )


class MavenRemote(Remote):
    """
    A Remote for Maven content.
    """

    TYPE = "maven"

    @staticmethod
    def get_remote_artifact_content_type(relative_path=None):
        """Return MavenArtifact for all Maven content."""
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
    CONTENT_TYPES = [MavenArtifact]
    REMOTE_TYPES = [MavenRemote]
    PULL_THROUGH_SUPPORTED = True

    def finalize_new_version(self, new_version):
        """Remove duplicate content when new content with same repo_key_fields is added."""
        remove_duplicates(new_version)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
