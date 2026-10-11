import re
import threading
from collections import defaultdict, namedtuple
from gettext import gettext as _
from logging import getLogger
from os import path

from django.contrib.postgres.indexes import GinIndex
from django.db import IntegrityError, connection, models, transaction
from django.db.models import Q
from django_lifecycle import AFTER_CREATE, AFTER_DELETE, AFTER_UPDATE, hook

from pulpcore.plugin.models import (
    AutoAddObjPermsMixin,
    Content,
    Distribution,
    Remote,
    Repository,
)
from pulpcore.plugin.repo_version_utils import remove_duplicates
from pulpcore.plugin.util import get_domain_pk

from pulp_maven.app.bloom import (
    BLOOM_FILTER_CONFIG_LABEL,
    bloom_filter_might_contain,
    delete_bloom_filter,
)

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

    @staticmethod
    def metadata_coordinates_from_path(relative_path):
        """
        Parse (group_id, artifact_id, version) for a ``maven-metadata.xml`` path.

        ``maven-metadata.xml`` is published at two levels: the artifact level
        (``<group>/<artifactId>/maven-metadata.xml``), which has no version, and the
        version level (``<group>/<artifactId>/<version>/maven-metadata.xml``), which
        only exists for SNAPSHOT versions. The directory segment directly above the
        file is therefore the artifactId unless it is a SNAPSHOT version. Relying on
        the directory structure — rather than the digit heuristic used for regular
        artifacts — avoids mis-reading a digit-containing artifactId (e.g. ``pop3``)
        as a version, which broke deduplication of ingested metadata (GH #503).

        Any trailing checksum extension (``.md5``/``.sha1``/``.sha256``/``.sha512``)
        is ignored so a checksum sibling resolves to the same coordinates as the
        ``maven-metadata.xml`` it describes.

        Args:
            relative_path (str): Relative path of a ``maven-metadata.xml`` file or
                one of its checksum siblings.

        Returns:
            Tuple (group_id, artifact_id, version)

        """
        base_path = relative_path
        for ext in (".md5", ".sha1", ".sha256", ".sha512"):
            if base_path.endswith(ext):
                base_path = base_path[: -len(ext)]
                break

        sub_path, _ = path.split(base_path)
        parent_dir, last_segment = path.split(sub_path)
        if last_segment.endswith("-SNAPSHOT"):
            group_path, artifact_id = path.split(parent_dir)
            version = last_segment
        else:
            group_path = parent_dir
            artifact_id = last_segment
            version = None
        return group_path.replace("/", "."), artifact_id, version


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
            elif path.basename(parent_path) == "maven-metadata.xml":
                # The parent maven-metadata.xml isn't ingested yet — derive
                # coordinates from the path structure so a digit-containing
                # artifactId isn't mistaken for a version (GH #503).
                group_id, artifact_id, version = MavenMetadata.metadata_coordinates_from_path(
                    relative_path
                )
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


class MavenPackage(Content):
    """
    A logical Maven package at the GAV (groupId, artifactId, version) level.

    Groups MavenArtifact files that share the same GAV coordinates.
    Created when a `.pom` file is saved (deploy API, REST upload).
    `finalize_new_version` creates missing packages as a fallback when a POM is available.
    SNAPSHOT versions are mutable — metadata is refreshed on each POM upload.
    """

    TYPE = "package"
    repo_key_fields = ("group_id", "artifact_id", "version")

    _pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.PROTECT)
    group_id = models.CharField(max_length=255, null=False)
    artifact_id = models.CharField(max_length=255, null=False)
    version = models.CharField(max_length=255, null=False)

    name = models.TextField(null=True)
    description = models.TextField(null=True)
    packaging = models.CharField(max_length=64, null=True)
    url = models.CharField(max_length=2048, null=True)
    licenses = models.JSONField(null=True)
    dependencies = models.JSONField(null=True)
    scm_url = models.CharField(max_length=2048, null=True)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        unique_together = ("group_id", "artifact_id", "version", "_pulp_domain")
        indexes = [  # noqa: RUF012
            GinIndex(
                fields=["group_id"],
                name="maven_pkg_group_id_trgm",
                opclasses=["gin_trgm_ops"],
            ),
            GinIndex(
                fields=["artifact_id"],
                name="maven_pkg_artifact_id_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ]

    def update_from_pom(self, artifact):
        """Parse POM XML from an artifact file and populate metadata fields."""
        from pulp_maven.app.pom import parse_pom_metadata

        try:
            with artifact.file.open("rb") as f:
                meta = parse_pom_metadata(f)
        except Exception:
            logger.warning("Failed to parse POM metadata from %s", artifact.file.name)
            return

        if meta is None:
            return

        self.name = meta["name"]
        self.description = meta["description"]
        self.packaging = meta["packaging"]
        self.url = meta["url"]
        self.licenses = meta["licenses"]
        self.dependencies = meta["dependencies"]
        self.scm_url = meta["scm_url"]


class MavenIndexPage(Content):
    """
    A pre-generated HTML directory index page.

    One per directory path. The associated ContentArtifact uses
    `relative_path = f"{path}index.html"`, which is exactly what the
    pulpcore handler looks up when a client requests a directory URL.
    Keyed on `path` so there is at most one live index page per directory.
    """

    TYPE = "index-page"
    repo_key_fields = ("path",)

    _pulp_domain = models.ForeignKey("core.Domain", default=get_domain_pk, on_delete=models.PROTECT)
    path = models.CharField(max_length=1024, null=False)
    sha256 = models.CharField(max_length=64, null=False, db_index=True)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        unique_together = ("path", "sha256", "_pulp_domain")


def _bulk_get_or_create_index_pages(dir_to_artifact, pulp_domain):
    """Return index page primary keys after creating missing pages in bulk."""
    page_pks = {}
    digests = list({artifact.sha256 for artifact in dir_to_artifact.values()})
    for offset in range(0, len(digests), 1000):
        existing_pages = MavenIndexPage.objects.filter(
            sha256__in=digests[offset : offset + 1000],
            _pulp_domain=pulp_domain,
        ).values_list("path", "sha256", "pk")
        page_pks.update({(path, sha256): pk for path, sha256, pk in existing_pages})

    missing_pages = [
        (path, artifact.sha256)
        for path, artifact in dir_to_artifact.items()
        if (path, artifact.sha256) not in page_pks
    ]
    if missing_pages:
        # Django does not support bulk_create() for multi-table inherited models.
        # Create the Content parents through the ORM, then insert the child-table
        # fields in true multi-row statements under the same transaction.
        parent_rows = []
        child_rows = []
        for path, sha256 in missing_pages:
            parent = Content(
                pulp_type=MavenIndexPage.get_pulp_type(),
                pulp_domain=pulp_domain,
            )
            parent_rows.append(parent)
            child_rows.append((parent.pk, path, sha256, pulp_domain.pk))

        table = connection.ops.quote_name(MavenIndexPage._meta.db_table)
        columns = (
            MavenIndexPage._meta.pk.column,
            MavenIndexPage._meta.get_field("path").column,
            MavenIndexPage._meta.get_field("sha256").column,
            MavenIndexPage._meta.get_field("_pulp_domain").column,
        )
        quoted_columns = ", ".join(connection.ops.quote_name(column) for column in columns)
        row_placeholders = f"({', '.join(['%s'] * len(columns))})"
        insert_prefix = f"INSERT INTO {table} ({quoted_columns}) VALUES "

        try:
            with transaction.atomic():
                Content.objects.bulk_create(parent_rows, batch_size=500)
                with connection.cursor() as cursor:
                    for offset in range(0, len(child_rows), 1000):
                        batch = child_rows[offset : offset + 1000]
                        values_sql = ", ".join([row_placeholders] * len(batch))
                        params = [value for row in batch for value in row]
                        cursor.execute(f"{insert_prefix}{values_sql}", params)
        except IntegrityError:
            # Another task can create a matching page after the initial lookup. The
            # batch transaction has rolled back, so resolve only this rare race with
            # the regular collision-safe save path.
            for path, sha256 in missing_pages:
                page = MavenIndexPage(
                    path=path,
                    sha256=sha256,
                    _pulp_domain=pulp_domain,
                )
                try:
                    with transaction.atomic():
                        page.save()
                except IntegrityError:
                    page = MavenIndexPage.objects.get(
                        path=path,
                        sha256=sha256,
                        _pulp_domain=pulp_domain,
                    )
                page_pks[(path, sha256)] = page.pk
        else:
            page_pks.update(
                {
                    (path, sha256): parent.pk
                    for (path, sha256), parent in zip(
                        missing_pages,
                        parent_rows,
                        strict=True,
                    )
                }
            )

    return {path: page_pks[(path, artifact.sha256)] for path, artifact in dir_to_artifact.items()}


class MavenRemote(Remote, AutoAddObjPermsMixin):
    """
    A Remote for MavenArtifact.

    Define any additional fields for your new importer if needed.
    """

    TYPE = "maven"

    @staticmethod
    def get_remote_artifact_content_type(relative_path=None):
        """
        Returns content type that is found at the relative_path.

        Returns None for maven-metadata.xml, its checksum sidecar files, and
        .meta/prefixes.txt so the pull-through handler streams them from the
        remote without saving locally.
        """
        if relative_path and (
            relative_path.endswith(
                (
                    "/maven-metadata.xml",
                    ".xml.md5",
                    ".xml.sha1",
                    ".xml.sha224",
                    ".xml.sha256",
                    ".xml.sha384",
                    ".xml.sha512",
                )
            )
            or relative_path == ".meta/prefixes.txt"
        ):
            return None
        return MavenArtifact

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        permissions = [  # noqa: RUF012
            ("manage_roles_mavenremote", "Can manage roles on Maven remote"),
        ]


class MavenDistribution(Distribution, AutoAddObjPermsMixin):
    """
    Distribution for 'maven' content.
    """

    TYPE = "maven"

    def content_handler(self, path):
        """Serve pre-generated HTML index pages inline, bypassing redirect-to-object-storage.

        When a client requests a directory URL, pulpcore's default path calls
        `_serve_content_artifact` which issues a 302 redirect to S3/Azure/GCS for the
        index.html artifact. That redirect changes the Content-Type to
        ``attachment`` and breaks browser rendering.

        Instead, read the small HTML bytes here and return an inline response so that
        directory listings are always served directly, regardless of storage backend.
        """
        from os.path import join

        from aiohttp.web import HTTPMovedPermanently, HTTPNotFound, Response

        from pulpcore.plugin.models import ContentArtifact

        from pulp_maven.app.path_index.content import indexed_response

        response = indexed_response(self, path)
        if response is not None:
            return response

        # Resolve the live repository version for this distribution.
        if self.repository_version_id:
            version = self.repository_version
        elif self.repository_id:
            version = self.repository.latest_version()
        else:
            return None

        if version is None:
            return None

        if self.repository_id and self.remote_id is None:
            # Check the bloom filter for the repository if configured
            if not bloom_filter_might_contain(
                self.repository, version, path, join(path, "index.html")
            ):
                # Cache the 404 response for the bloom filtered path
                class BloomFiltered(HTTPNotFound):
                    cacheable = True

                raise BloomFiltered(headers={"X-Pulp-Bloom-Filtered": "True"})
        # For paths WITHOUT a trailing slash, check whether a pre-generated index page
        # exists.  If so issue a redirect to the trailing-slash form; the next request
        # will be served inline by the branch below.  The normal fallback (line ~851 in
        # handler.py) only does this redirect when using on-demand list_directory(), not
        # when an index.html ContentArtifact is found, so we handle it here instead.
        if path and not path.endswith("/"):
            # Skip the DB lookup for obvious file requests (any path whose last segment
            # contains a dot — .jar, .pom, .sha1, .xml, etc.).  content_handler is called
            # for every request, so this avoids a wasted ContentArtifact query per download.
            last_segment = path.rsplit("/", 1)[-1] if "/" in path else path
            if "." in last_segment:
                return None

            has_index = ContentArtifact.objects.filter(
                content__in=version.content,
                content__pulp_type="maven.index-page",
                relative_path=f"{path}/index.html",
            ).exists()
            if has_index:
                raise HTTPMovedPermanently(f"{last_segment}/")
            return None

        # For paths WITH a trailing slash (or the distribution root ""), serve the
        # pre-generated index page inline as text/html, bypassing _serve_content_artifact
        # which would issue a 302 redirect to object storage.
        index_rel_path = f"{path}index.html"
        ca = (
            ContentArtifact.objects.filter(
                content__in=version.content,
                content__pulp_type="maven.index-page",
                relative_path=index_rel_path,
            )
            .select_related("artifact")
            .first()
        )

        if ca is None or ca.artifact is None:
            return None

        with ca.artifact.file.open("rb") as fh:
            html_bytes = fh.read()

        return Response(
            body=html_bytes,
            content_type="text/html",
            charset="utf-8",
            headers={
                "ETag": f'"{ca.artifact.sha256}"',
                "Cache-Control": "public, max-age=0, must-revalidate",
            },
        )

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        permissions = [  # noqa: RUF012
            ("manage_roles_mavendistribution", "Can manage roles on Maven distribution"),
        ]


class MavenRepository(Repository, AutoAddObjPermsMixin):
    """
    Repository for "maven" content.
    """

    TYPE = "maven"
    CONTENT_TYPES = [MavenArtifact, MavenMetadata, MavenPackage, MavenIndexPage]  # noqa: RUF012
    REMOTE_TYPES = [MavenRemote]  # noqa: RUF012
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
        """Remove duplicates, ensure packages, and generate metadata and index pages."""
        remove_duplicates(new_version)
        if not getattr(_pull_through_ctx, "active", False):
            self._ensure_packages(new_version)
            self._generate_metadata(new_version)
            self._generate_index_pages(new_version)
            self._generate_bloom_filter(new_version)

        from pulp_maven.app.path_index.publish import finalize

        finalize(self, new_version)

    @hook(AFTER_CREATE)
    @hook(AFTER_UPDATE, when="pulp_labels", has_changed=True)
    def update_bloom_filter(self):
        """Update the Bloom filter for the repository if configured."""
        from pulp_maven.app.tasks import generate_bloom_filter

        if self.pulp_labels.get(BLOOM_FILTER_CONFIG_LABEL):
            generate_bloom_filter(self.pk)
        else:
            delete_bloom_filter(self)

    @hook(AFTER_DELETE)
    def delete_redis_bloom_filter(self):
        """Remove the repository's Bloom filter from Redis."""
        delete_bloom_filter(self)

    def _ensure_packages(self, new_version):
        """Manage MavenPackage version membership. Creates missing packages when a POM is available."""
        from django.db.models import Q

        from pulpcore.plugin.models import ContentArtifact

        affected_gavs = set()
        for qs in (
            MavenArtifact.objects.filter(pk__in=new_version.added()),
            MavenArtifact.objects.filter(pk__in=new_version.removed()),
        ):
            for vals in qs.values("group_id", "artifact_id", "version").distinct().iterator():
                affected_gavs.add((vals["group_id"], vals["artifact_id"], vals["version"]))

        if not affected_gavs:
            return

        gavs_q = Q()
        for g, a, v in affected_gavs:
            gavs_q |= Q(group_id=g, artifact_id=a, version=v)

        live_gavs = set(
            MavenArtifact.objects.filter(pk__in=new_version.content)
            .filter(gavs_q)
            .values_list("group_id", "artifact_id", "version")
            .distinct()
        )

        existing_pkgs = {
            (p.group_id, p.artifact_id, p.version): p
            for p in MavenPackage.objects.filter(gavs_q, _pulp_domain=self.pulp_domain)
        }

        gavs_needing_pom = {
            gav for gav in live_gavs if gav not in existing_pkgs or gav[2].endswith("-SNAPSHOT")
        }

        pom_cas = {}
        if gavs_needing_pom:
            pom_q = Q()
            for g, a, v in gavs_needing_pom:
                pom_q |= Q(
                    group_id=g,
                    artifact_id=a,
                    version=v,
                    filename=f"{a}-{v}.pom",
                )
            pom_content_to_gav = {}
            for ma in (
                MavenArtifact.objects.filter(pk__in=new_version.content).filter(pom_q).iterator()
            ):
                pom_content_to_gav[ma.pk] = (
                    ma.group_id,
                    ma.artifact_id,
                    ma.version,
                )
            for ca in (
                ContentArtifact.objects.filter(content_id__in=pom_content_to_gav.keys())
                .select_related("artifact")
                .iterator()
            ):
                gav = pom_content_to_gav.get(ca.content_id)
                if gav and ca.artifact:
                    pom_cas[gav] = ca

        package_pks_to_add = []
        for gav in live_gavs:
            pkg = existing_pkgs.get(gav)
            if pkg and gav not in gavs_needing_pom:
                package_pks_to_add.append(pkg.pk)
                continue

            ca = pom_cas.get(gav)
            if not ca:
                if pkg:
                    package_pks_to_add.append(pkg.pk)
                continue

            pkg, created = MavenPackage.objects.get_or_create(
                group_id=gav[0],
                artifact_id=gav[1],
                version=gav[2],
                _pulp_domain=self.pulp_domain,
            )
            if created or gav[2].endswith("-SNAPSHOT"):
                pkg.update_from_pom(ca.artifact)
                pkg.save()
            package_pks_to_add.append(pkg.pk)

        if package_pks_to_add:
            new_version.add_content(MavenPackage.objects.filter(pk__in=package_pks_to_add))

        dead_gavs = affected_gavs - live_gavs
        if dead_gavs:
            dead_q = Q()
            for g, a, v in dead_gavs:
                dead_q |= Q(group_id=g, artifact_id=a, version=v)
            dead_pkgs = MavenPackage.objects.filter(
                pk__in=new_version.content, _pulp_domain=self.pulp_domain
            ).filter(dead_q)
            new_version.remove_content(dead_pkgs)

    def _generate_metadata(self, new_version):
        """Generate maven-metadata.xml and checksums for affected (group_id, artifact_id) pairs."""
        from collections import defaultdict

        from django.db.models import Q

        from pulp_maven.app.tasks import (
            METADATA_FILENAMES,
            PREFIXES_TXT_FILENAME,
            _compute_prefix,
            _create_metadata_content,
            _create_version_level_metadata_content,
            _save_prefixes_txt,
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

        # --- prefixes.txt generation ---
        # Compute prefixes from the affected group_ids. If the set of
        # prefixes in the repository changed (new prefixes appeared or an
        # existing prefix lost all its artifacts), regenerate the file.

        # Changed artifact prefixes (from removed or added artifacts)
        affected_prefixes = {_compute_prefix(gid) for gid, _ in affected_pairs}

        # Get all group_ids currently in the version (across ALL artifacts,
        # not just the affected ones) so we can check whether the affected
        # prefixes are truly new or truly gone.
        all_group_ids = set(
            MavenArtifact.objects.filter(pk__in=new_version.content)
            .values_list("group_id", flat=True)
            .distinct()
        )
        # All prefixes in the repo now
        current_prefixes = {_compute_prefix(gid) for gid in all_group_ids}

        unaffected_group_ids = set(
            MavenArtifact.objects.filter(pk__in=new_version.content)
            .exclude(pairs_q)
            .values_list("group_id", flat=True)
            .distinct()
        )

        unaffected_prefixes = {_compute_prefix(gid) for gid in unaffected_group_ids}

        # affected prefixes not in unaffected prefix list? -> prefixes added
        # affected prefixes not in current prefix list? -> prefixes removed
        prefix_set_changed = bool(affected_prefixes - unaffected_prefixes) or bool(
            affected_prefixes - current_prefixes
        )

        # Also check if this repository version has a prefixes.txt file.
        # This catches the scenario where this logic runs for the first time
        # on a repository that has never had a 'prefixes.txt' file and adds
        # an artifact under an already existing prefix, so no file is generated.
        old_prefixes_pks = list(
            MavenMetadata.objects.filter(
                pk__in=new_version.content,
                filename=PREFIXES_TXT_FILENAME,
            ).values_list("pk", flat=True)
        )
        if prefix_set_changed or not old_prefixes_pks:
            # Remove old prefixes.txt from the version
            if old_prefixes_pks:
                new_version.remove_content(MavenMetadata.objects.filter(pk__in=old_prefixes_pks))

            if current_prefixes:
                prefixes_pks = _save_prefixes_txt(current_prefixes, self.pulp_domain)
                new_version.add_content(MavenMetadata.objects.filter(pk__in=prefixes_pks))

    def _generate_index_pages(self, new_version, affected_paths=None):
        """Generate HTML index pages for directory paths touched by this version.

        Args:
            new_version: The repository version being finalised.
            affected_paths: Optional set of directory path strings (each ending in ``/``
                or the empty string ``""`` for the root). When *None* (the default),
                the set is computed from ``new_version.added()`` and
                ``new_version.removed()`` — used during normal ``finalize_new_version``.
                Pass an explicit set (e.g. from ``repair_index_pages``) to regenerate a
                specific or complete collection of directories regardless of the diff.
        """

        from pulpcore.plugin.content import Handler
        from pulpcore.plugin.models import (
            Artifact,
            ContentArtifact,
            RemoteArtifact,
            RepositoryContent,
        )

        from pulp_maven.app.tasks import _parse_index_pages, _save_artifacts_batch

        def file_or_directory_name(directory_path, relative_path):
            result = re.match(r"({})([^\/]*)(\/*)".format(re.escape(directory_path)), relative_path)
            return "{}{}".format(result.groups()[1], result.groups()[2])

        def ancestor_directory_paths(directory_path):
            yield ""
            parts = directory_path.rstrip("/").split("/") if directory_path else []
            for index in range(1, len(parts) + 1):
                yield "/".join(parts[:index]) + "/"

        # Track whether the caller passed an explicit path set (the repair case).
        # The two strategies below are chosen based on this flag.
        bulk_mode = affected_paths is not None
        MARTIFACT_TYPE = MavenArtifact.get_pulp_type()
        METADATA_TYPE = MavenMetadata.get_pulp_type()

        AddPath = namedtuple("AddPath", ["relative_path", "artifact_size"])
        RemovePath = namedtuple("RemovePath", ["relative_path", "artifact_size"])

        if affected_paths is None:
            # Compute all ancestor directory paths for added/removed non-index content.
            pulp_type_q = Q(content__pulp_type__in=[MARTIFACT_TYPE, METADATA_TYPE])
            added_pks = RepositoryContent.objects.filter(
                pulp_type_q,
                repository=self,
                version_added=new_version,
            ).values_list("content_id", flat=True)
            removed_pks = RepositoryContent.objects.filter(
                pulp_type_q,
                repository=self,
                version_removed=new_version,
            ).values_list("content_id", flat=True)

            all_content_pks = added_pks | removed_pks
            if not all_content_pks.exists():
                return
            affected_paths = defaultdict(list)
            for pks, path_type in ((added_pks, AddPath), (removed_pks, RemovePath)):
                changed_paths = (
                    ContentArtifact.objects.filter(content_id__in=pks)
                    .values_list("relative_path", "artifact__size")
                    .iterator()
                )
                for relative_path, artifact_size in changed_paths:
                    parts = relative_path.split("/")
                    for i in range(len(parts)):
                        directory_path = "" if i == 0 else "/".join(parts[:i]) + "/"
                        affected_paths[directory_path].append(
                            path_type(relative_path, artifact_size)
                        )

        if not affected_paths:
            return

        # Pre-fetch all existing index pages so we can remove stale ones without
        # issuing one EXISTS query per directory.
        existing_indexes_qs = MavenIndexPage.objects.filter(pk__in=new_version.content).only(
            "path", "pk", "sha256", "pulp_type"
        )
        if not bulk_mode:
            existing_indexes_qs = existing_indexes_qs.filter(path__in=affected_paths)
        existing_indexes = {
            index_page.path: index_page for index_page in existing_indexes_qs.iterator()
        }

        if bulk_mode:
            # Bulk mode: load rc_dates for all content in the version upfront.
            # Acceptable here because repair already touches the full version.
            rc_dates = {
                rc.content_id: rc.pulp_created
                for rc in new_version._content_relationships().only("content_id", "pulp_created")
            }
            # Repair / full-repository path: fetch ALL ContentArtifacts in one query,
            # build all directory listings in Python, batch-save artifacts, then
            # batch-write DB rows.
            all_cas = list(
                ContentArtifact.objects.select_related("artifact")
                .filter(content__in=new_version.content)
                .exclude(content__pulp_type="maven.index-page")
            )

            # Resolve on-demand sizes (RemoteArtifact) in one query.
            ca_pks_without_artifact = [ca.pk for ca in all_cas if not ca.artifact]
            remote_sizes: dict = {}
            if ca_pks_without_artifact:
                for ra_ca_id, size in RemoteArtifact.objects.filter(
                    content_artifact__in=ca_pks_without_artifact, size__isnull=False
                ).values_list("content_artifact_id", "size"):
                    remote_sizes[ra_ca_id] = size

            # Build per-directory data in one pass (pure CPU, no I/O).
            dir_entries: dict = {dp: {} for dp in affected_paths}

            for ca in all_cas:
                parts = ca.relative_path.split("/")
                ca_size = ca.artifact.size if ca.artifact else remote_sizes.get(ca.pk)
                ca_date = rc_dates.get(ca.content_id, ca.pulp_created)

                for i in range(len(parts)):
                    dir_path = "" if i == 0 else "/".join(parts[:i]) + "/"
                    if dir_path not in dir_entries:
                        continue
                    name = (parts[i] + "/") if i + 1 < len(parts) else parts[i]
                    if not name:
                        continue
                    dir_entries[dir_path].setdefault(name, {"size": ca_size, "date": ca_date})
                    if ca_date > dir_entries[dir_path][name]["date"]:
                        dir_entries[dir_path][name]["date"] = ca_date

            # Render HTML for every directory (CPU-only).
            pages_to_save: list = []
            pages_to_remove: set = set()
            for dir_path, entries in dir_entries.items():
                if dir_path in existing_indexes:
                    pages_to_remove.add(existing_indexes[dir_path].pk)
                directory_list = set(entries.keys())
                if not directory_list:
                    continue
                dates = {name: e["date"] for name, e in entries.items()}
                sizes = {
                    name: e["size"]
                    for name, e in entries.items()
                    if e["size"] is not None and not name.endswith("/")
                }
                html_bytes = Handler.render_html(
                    directory_list, path=dir_path, dates=dates, sizes=sizes
                ).encode("utf-8")
                pages_to_save.append((dir_path, html_bytes))

            # Batch-save artifacts: one IN query per 1 000 sha256s to find existing
            # artifacts, then temp-file + upload only for genuinely new content.
            # Follows the same pattern as pulpcore's sync pipeline ArtifactSaver stage.
            dir_to_artifact = _save_artifacts_batch(pages_to_save, self.pulp_domain)

            # Batch DB writes: Content parents, MavenIndexPage children,
            # ContentArtifacts, and one add_content call for the entire set.
            dir_to_page_pk = _bulk_get_or_create_index_pages(
                dir_to_artifact,
                self.pulp_domain,
            )
            new_page_pks = list(dir_to_page_pk.values())
            cas_to_create = [
                ContentArtifact(
                    artifact=artifact,
                    content_id=dir_to_page_pk[dir_path],
                    relative_path=f"{dir_path}index.html",
                )
                for dir_path, artifact in dir_to_artifact.items()
            ]

            ContentArtifact.objects.bulk_create(cas_to_create, ignore_conflicts=True)
            if pages_to_remove:
                new_version.remove_content(MavenIndexPage.objects.filter(pk__in=pages_to_remove))
            if new_page_pks:
                new_version.add_content(MavenIndexPage.objects.filter(pk__in=new_page_pks))
            return

        else:
            # Incremental path (finalize_new_version): determine the existing pages that will be
            # modified and the new pages that will be created. For existing pages, parse the current
            # HTML, then rebuild the HTML with the modified paths. If the rebuilt page would be empty,
            # add it to the list of pages to remove. After new pages and modified pages are built,
            # batch-save the artifacts and create the MavenIndexPage records.

            modified_index_paths = affected_paths.keys() & existing_indexes.keys()

            # Fetch all backing artifacts directly through their domain-scoped digests.
            digest_to_paths = defaultdict(list)
            for path in modified_index_paths:
                page = existing_indexes[path]
                digest_to_paths[page.sha256].append(path)

            artifacts_by_digest = {}
            digests = list(digest_to_paths)
            for offset in range(0, len(digests), 1000):
                artifacts = (
                    Artifact.objects.filter(
                        sha256__in=digests[offset : offset + 1000],
                        pulp_domain=self.pulp_domain,
                    )
                    .only("pk", "sha256", "file", "pulp_domain")
                    .iterator()
                )
                artifacts_by_digest.update({artifact.sha256: artifact for artifact in artifacts})

            # Missing and unreadable pages are optional optimization failures. Remove
            # them and skip affected ancestors rather than failing repository creation.
            missing = digest_to_paths.keys() - artifacts_by_digest.keys()
            for digest in missing:
                logger.warning(
                    "Skipping Maven index pages with missing Artifact sha256=%s paths=%s",
                    digest,
                    digest_to_paths[digest],
                )

            # Parse the existing index pages from their artifacts
            page_sources = [
                (digest, artifacts_by_digest[digest])
                for digest in digest_to_paths
                if digest in artifacts_by_digest
            ]
            parsed_pages_by_digest, parse_failures = _parse_index_pages(page_sources)
            for digest, exc in parse_failures.items():
                logger.warning(
                    "Skipping unreadable Maven index pages sha256=%s paths=%s: %s",
                    digest,
                    digest_to_paths[digest],
                    exc,
                )

            unavailable_paths = {
                directory_path
                for digest in missing | parse_failures.keys()
                for directory_path in digest_to_paths[digest]
            }
            skipped_paths = {
                ancestor
                for directory_path in unavailable_paths
                for ancestor in ancestor_directory_paths(directory_path)
                if ancestor in affected_paths
            }

            # Start with parsed state for existing pages and empty state for new pages.
            page_states = {
                directory_path: {}
                for directory_path in affected_paths
                if directory_path not in skipped_paths
            }
            for digest, parsed_entries in parsed_pages_by_digest.items():
                parsed_state = {
                    entry.name: {"date": entry.modified, "size": entry.size}
                    for entry in parsed_entries
                }
                for directory_path in digest_to_paths[digest]:
                    if directory_path not in page_states:
                        continue
                    page_states[directory_path] = {
                        name: values.copy() for name, values in parsed_state.items()
                    }

            # Remove direct files first. Directory removals are determined bottom-up,
            # after checking whether their child page actually became empty.
            for directory_path, changes in affected_paths.items():
                if directory_path not in page_states:
                    continue
                entries = page_states[directory_path]
                for changed_path in changes:
                    if not isinstance(changed_path, RemovePath):
                        continue
                    name = file_or_directory_name(directory_path, changed_path.relative_path)
                    if not name.endswith("/"):
                        entries.pop(name, None)

                # Apply additions after removals so replacing content at the same path
                # leaves the path present with the new size and timestamp.
                for changed_path in changes:
                    if not isinstance(changed_path, AddPath):
                        continue
                    name = file_or_directory_name(directory_path, changed_path.relative_path)
                    entry = entries.setdefault(name, {"date": None, "size": None})
                    entry["date"] = new_version.pulp_created
                    if not name.endswith("/"):
                        entry["size"] = changed_path.artifact_size

            # Remove empty child directories from their parents, deepest first.
            for directory_path in sorted(
                page_states,
                key=lambda value: value.count("/"),
                reverse=True,
            ):
                if not directory_path:
                    continue
                child_path = directory_path.rstrip("/")
                parent_prefix, _, child_name = child_path.rpartition("/")
                parent_path = f"{parent_prefix}/" if parent_prefix else ""
                if parent_path not in page_states:
                    continue
                child_entry_name = f"{child_name}/"
                child_entries = page_states[directory_path]
                if not child_entries:
                    page_states[parent_path].pop(child_entry_name, None)
                    continue

                child_dates = [
                    entry["date"] for entry in child_entries.values() if entry["date"] is not None
                ]
                child_date = (
                    max(child_dates, key=lambda value: value.replace(tzinfo=None))
                    if child_dates
                    else None
                )
                page_states[parent_path][child_entry_name] = {
                    "date": child_date,
                    "size": None,
                }

            pages_to_remove = {
                existing_indexes[directory_path].pk
                for directory_path in affected_paths
                if directory_path in existing_indexes
            }
            pages_to_save = []
            for directory_path, entries in page_states.items():
                if not entries:
                    continue
                directory_list = set(entries)
                dates = {name: entry["date"] for name, entry in entries.items()}
                sizes = {
                    name: entry["size"]
                    for name, entry in entries.items()
                    if entry["size"] is not None and not name.endswith("/")
                }
                html_bytes = Handler.render_html(
                    directory_list,
                    path=directory_path,
                    dates=dates,
                    sizes=sizes,
                ).encode("utf-8")
                pages_to_save.append((directory_path, html_bytes))

            # Pass 3: batch-save page artifacts (same path as repair_index_pages).
            dir_to_artifact = _save_artifacts_batch(pages_to_save, self.pulp_domain)

            # Pass 4: write DB records.
            dir_to_page_pk = _bulk_get_or_create_index_pages(
                dir_to_artifact,
                self.pulp_domain,
            )
            new_page_pks = list(dir_to_page_pk.values())
            cas_to_create = [
                ContentArtifact(
                    artifact=artifact,
                    content_id=dir_to_page_pk[dir_path],
                    relative_path=f"{dir_path}index.html",
                )
                for dir_path, artifact in dir_to_artifact.items()
            ]
            ContentArtifact.objects.bulk_create(cas_to_create, ignore_conflicts=True)
            if pages_to_remove:
                new_version.remove_content(MavenIndexPage.objects.filter(pk__in=pages_to_remove))
            if new_page_pks:
                new_version.add_content(MavenIndexPage.objects.filter(pk__in=new_page_pks))

    def _generate_bloom_filter(self, new_version):
        """Generate the Bloom filter for the repository if configured."""
        from pulp_maven.app.tasks import _generate_bloom_filter

        _generate_bloom_filter(self, new_version)

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        permissions = [  # noqa: RUF012
            ("modify_mavenrepository", "Can modify content in Maven repository"),
            ("manage_roles_mavenrepository", "Can manage roles on Maven repository"),
            ("repair_mavenrepository", "Can repair Maven repository metadata"),
        ]
