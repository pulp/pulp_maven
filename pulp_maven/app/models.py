import re
import threading
from gettext import gettext as _
from logging import getLogger
from os import path

from django.db import models

from pulpcore.plugin.models import Content, Distribution, Remote, Repository
from pulpcore.plugin.repo_version_utils import remove_duplicates
from pulpcore.plugin.util import get_domain_pk

logger = getLogger(__name__)

# Thread-local used to skip metadata generation during pull-through caching.
# Pull-through tasks run asynchronously from the content app; any extra work in
# finalize_new_version delays version creation and races with subsequent reads.
_pull_through_ctx = threading.local()


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
        pattern = re.compile(r"(?=.*\d)[a-zA-Z0-9]+([.-][a-zA-Z0-9]+)*")
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
    repo_key_fields = ("group_id", "artifact_id", "version", "filename")

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
    repo_key_fields = ("group_id", "artifact_id", "version", "filename")

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
        import defusedxml.ElementTree as ET

        from pulpcore.plugin.models import ContentArtifact

        if path.isabs(relative_path):
            raise ValueError(_("Relative path can't start with '/'."))

        _, _, _, f_name = MavenMetadata.group_artifact_version_filename(relative_path)

        if f_name == "maven-metadata.xml":
            try:
                with artifact.file.open("rb") as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    group_id = root.findtext("groupId", "")
                    artifact_id = root.findtext("artifactId", "")
                    version = root.findtext("version")
            except ET.ParseError:
                raise ValueError("maven-metadata.xml could not be parsed as valid XML.")
        else:
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
                group_id, artifact_id, version, _ = MavenMetadata.group_artifact_version_filename(
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

        Returns None for maven-metadata.xml and its checksum sidecar files so the
        pull-through handler streams them from the remote without saving locally.
        """
        if relative_path and relative_path.endswith(
            (
                "/maven-metadata.xml",
                ".xml.md5",
                ".xml.sha1",
                ".xml.sha224",
                ".xml.sha256",
                ".xml.sha384",
                ".xml.sha512",
            )
        ):
            return None
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
    PULL_THROUGH_SUPPORTED = True

    def pull_through_add_content(self, content_artifact):
        """Use a task that skips metadata generation for pull-through content."""
        from pulpcore.plugin.models import RepositoryContent

        cpk = content_artifact.content_id
        already_present = RepositoryContent.objects.filter(
            content__pk=cpk, repository=self, version_removed__isnull=True
        )
        if not cpk or already_present.exists():
            return None

        from pulpcore.plugin.tasking import dispatch

        from pulp_maven.app.tasks import pull_through_aadd_and_remove

        body = {
            "repository_pk": self.pk,
            "add_content_units": [cpk],
            "remove_content_units": [],
        }
        return dispatch(
            pull_through_aadd_and_remove,
            kwargs=body,
            exclusive_resources=[self],
            immediate=True,
        )

    async def async_pull_through_add_content(self, content_artifact):
        """Use a task that skips metadata generation for pull-through content."""
        from pulpcore.plugin.models import RepositoryContent

        cpk = content_artifact.content_id
        already_present = RepositoryContent.objects.filter(
            content__pk=cpk, repository=self, version_removed__isnull=True
        )
        if not cpk or await already_present.aexists():
            return None

        from pulpcore.plugin.tasking import adispatch

        from pulp_maven.app.tasks import pull_through_aadd_and_remove

        body = {
            "repository_pk": self.pk,
            "add_content_units": [cpk],
            "remove_content_units": [],
        }
        return await adispatch(
            pull_through_aadd_and_remove,
            kwargs=body,
            exclusive_resources=[self],
            immediate=True,
        )

    def finalize_new_version(self, new_version):
        """Remove duplicates and generate metadata for affected artifacts."""
        remove_duplicates(new_version)
        if not getattr(_pull_through_ctx, "active", False):
            self._generate_metadata(new_version)

    def _generate_metadata(self, new_version):
        """Generate maven-metadata.xml and checksums for affected (group_id, artifact_id) pairs."""
        from collections import defaultdict

        from django.db.models import Q

        from pulp_maven.app.tasks import (
            METADATA_FILENAMES,
            _create_metadata_content,
            _create_version_level_metadata_content,
        )

        affected_pairs = set()
        affected_snapshot_triples = set()
        for qs in (
            MavenArtifact.objects.filter(pk__in=new_version.added()),
            MavenArtifact.objects.filter(pk__in=new_version.removed()),
        ):
            for vals in qs.values("group_id", "artifact_id", "version").distinct().iterator():
                affected_pairs.add((vals["group_id"], vals["artifact_id"]))
                if vals["version"] and vals["version"].endswith("-SNAPSHOT"):
                    affected_snapshot_triples.add(
                        (vals["group_id"], vals["artifact_id"], vals["version"])
                    )

        if not affected_pairs:
            return

        pairs_q = Q()
        for group_id, artifact_id in affected_pairs:
            pairs_q |= Q(group_id=group_id, artifact_id=artifact_id)

        stale_pks = list(
            MavenMetadata.objects.filter(
                pk__in=new_version.content,
                version=None,
                filename__in=METADATA_FILENAMES,
            )
            .filter(pairs_q)
            .values_list("pk", flat=True)
        )
        if stale_pks:
            new_version.remove_content(MavenMetadata.objects.filter(pk__in=stale_pks))

        versions_by_pair = defaultdict(set)
        for row in (
            MavenArtifact.objects.filter(pk__in=new_version.content)
            .filter(pairs_q)
            .values("group_id", "artifact_id", "version")
            .distinct()
        ):
            versions_by_pair[(row["group_id"], row["artifact_id"])].add(row["version"])

        new_metadata_pks = []

        for (group_id, artifact_id), version_set in versions_by_pair.items():
            versions = sorted(version_set)
            if not versions:
                continue
            new_metadata_pks.extend(
                _create_metadata_content(group_id, artifact_id, versions, self.pulp_domain)
            )

        if affected_snapshot_triples:
            triples_q = Q()
            for group_id, artifact_id, version in affected_snapshot_triples:
                triples_q |= Q(group_id=group_id, artifact_id=artifact_id, version=version)

            stale_version_pks = list(
                MavenMetadata.objects.filter(
                    pk__in=new_version.content,
                    filename__in=METADATA_FILENAMES,
                )
                .filter(triples_q)
                .values_list("pk", flat=True)
            )
            if stale_version_pks:
                new_version.remove_content(MavenMetadata.objects.filter(pk__in=stale_version_pks))

            for group_id, artifact_id, version in affected_snapshot_triples:
                filenames = list(
                    MavenArtifact.objects.filter(
                        pk__in=new_version.content,
                        group_id=group_id,
                        artifact_id=artifact_id,
                        version=version,
                    ).values_list("filename", flat=True)
                )
                if not filenames:
                    continue
                new_metadata_pks.extend(
                    _create_version_level_metadata_content(
                        group_id, artifact_id, version, filenames, self.pulp_domain
                    )
                )

        if new_metadata_pks:
            new_version.add_content(MavenMetadata.objects.filter(pk__in=new_metadata_pks))

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
