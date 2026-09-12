import re
import threading
from gettext import gettext as _
from logging import getLogger
from os import path

from django.db import IntegrityError, models, transaction

from pulpcore.plugin.models import (
    AutoAddObjPermsMixin,
    Content,
    Distribution,
    Remote,
    Repository,
)
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
        from aiohttp.web import HTTPMovedPermanently, Response

        from pulpcore.plugin.models import ContentArtifact

        # Resolve the live repository version for this distribution.
        if self.repository_version_id:
            version = self.repository_version
        elif self.repository_id:
            version = self.repository.latest_version()
        else:
            return None

        if version is None:
            return None

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

        return Response(body=html_bytes, content_type="text/html", charset="utf-8")

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
        from pulpcore.plugin.models import ContentArtifact, RemoteArtifact

        from pulp_maven.app.tasks import _save_artifact, _save_artifacts_batch

        # Track whether the caller passed an explicit path set (the repair case).
        # The two strategies below are chosen based on this flag.
        bulk_mode = affected_paths is not None

        if affected_paths is None:
            # Compute all ancestor directory paths for added/removed non-index content.
            added_pks = set(
                MavenArtifact.objects.filter(pk__in=new_version.added()).values_list(
                    "pk", flat=True
                )
            ) | set(
                MavenMetadata.objects.filter(pk__in=new_version.added()).values_list(
                    "pk", flat=True
                )
            )
            removed_pks = set(
                MavenArtifact.objects.filter(pk__in=new_version.removed()).values_list(
                    "pk", flat=True
                )
            ) | set(
                MavenMetadata.objects.filter(pk__in=new_version.removed()).values_list(
                    "pk", flat=True
                )
            )

            all_content_pks = added_pks | removed_pks
            if not all_content_pks:
                return

            affected_paths = set()
            for (relative_path,) in ContentArtifact.objects.filter(
                content_id__in=all_content_pks
            ).values_list("relative_path"):
                parts = relative_path.split("/")
                for i in range(len(parts)):
                    affected_paths.add("" if i == 0 else "/".join(parts[:i]) + "/")

        if not affected_paths:
            return

        # Pre-fetch RepositoryContent dates once (one query instead of one per directory).
        rc_dates = {
            rc.content_id: rc.pulp_created
            for rc in new_version._content_relationships().only("content_id", "pulp_created")
        }

        # Pre-fetch all existing index pages so we can remove stale ones without
        # issuing one EXISTS query per directory.
        existing_index_pks = {
            row["path"]: row["pk"]
            for row in MavenIndexPage.objects.filter(pk__in=new_version.content).values(
                "path", "pk"
            )
        }

        if bulk_mode:
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
                    # Last CA for this name wins (matches list_directory() behaviour).
                    dir_entries[dir_path][name] = {
                        "content_id": ca.content_id,
                        "size": ca_size,
                        "date": ca_date,
                    }

            # Remove stale index pages and render HTML for every directory (CPU-only).
            pages_to_save: list = []
            for dir_path, entries in dir_entries.items():
                if dir_path in existing_index_pks:
                    new_version.remove_content(
                        MavenIndexPage.objects.filter(pk=existing_index_pks[dir_path])
                    )
                directory_list = set(entries.keys())
                if not directory_list:
                    continue
                dates = {name: e["date"] for name, e in entries.items()}
                sizes = {name: e["size"] for name, e in entries.items() if e["size"] is not None}
                html_bytes = Handler.render_html(
                    directory_list, path=dir_path, dates=dates, sizes=sizes
                ).encode("utf-8")
                pages_to_save.append((dir_path, html_bytes))

            # Batch-save artifacts: one IN query per 1 000 sha256s to find existing
            # artifacts, then temp-file + upload only for genuinely new content.
            # Follows the same pattern as pulpcore's sync pipeline ArtifactSaver stage.
            dir_to_artifact = _save_artifacts_batch(pages_to_save, self.pulp_domain)

            # Batch DB writes: one MavenIndexPage per directory, bulk ContentArtifacts,
            # one add_content call for the entire set.
            new_page_pks: list = []
            cas_to_create: list = []
            for dir_path, artifact in dir_to_artifact.items():
                page = MavenIndexPage(
                    path=dir_path,
                    sha256=artifact.sha256,
                    _pulp_domain=self.pulp_domain,
                )
                try:
                    with transaction.atomic():
                        page.save()
                except IntegrityError:
                    page = MavenIndexPage.objects.get(
                        path=dir_path,
                        sha256=artifact.sha256,
                        _pulp_domain=self.pulp_domain,
                    )
                new_page_pks.append(page.pk)
                cas_to_create.append(
                    ContentArtifact(
                        artifact=artifact,
                        content=page,
                        relative_path=f"{dir_path}index.html",
                    )
                )

            ContentArtifact.objects.bulk_create(cas_to_create, ignore_conflicts=True)
            if new_page_pks:
                new_version.add_content(MavenIndexPage.objects.filter(pk__in=new_page_pks))
            return

        else:
            # Incremental path (finalize_new_version with a small diff): issue one
            # targeted query per affected directory.  Loading all CAs for the entire
            # version would be wasteful when only a handful of paths changed.
            def _incremental_iter():
                for dir_path in affected_paths:
                    cas = (
                        ContentArtifact.objects.select_related("artifact")
                        .filter(
                            content__in=new_version.content,
                            relative_path__startswith=dir_path,
                        )
                        .exclude(content__pulp_type="maven.index-page")
                    )
                    import re as _re

                    pattern = _re.compile(r"({})([^\/]*)(\/*)".format(_re.escape(dir_path)))
                    entries = {}
                    artifacts_to_find = {}
                    for ca in cas:
                        m = pattern.match(ca.relative_path)
                        if not m:
                            continue
                        name = "{}{}".format(m.group(2), m.group(3))
                        if not name:
                            continue
                        ca_size = ca.artifact.size if ca.artifact else None
                        if ca_size is None:
                            artifacts_to_find[ca.pk] = name
                        entries[name] = {
                            "content_id": ca.content_id,
                            "size": ca_size,
                            "date": rc_dates.get(ca.content_id, ca.pulp_created),
                        }
                    if artifacts_to_find:
                        for ra_ca_id, size in RemoteArtifact.objects.filter(
                            content_artifact__in=artifacts_to_find.keys(), size__isnull=False
                        ).values_list("content_artifact_id", "size"):
                            entries[artifacts_to_find[ra_ca_id]]["size"] = size
                    yield dir_path, entries

            dir_iter = _incremental_iter()

        for dir_path, entries in dir_iter:
            # Remove the stale index page (uses pre-fetched dict, no extra DB query).
            if dir_path in existing_index_pks:
                new_version.remove_content(
                    MavenIndexPage.objects.filter(pk=existing_index_pks[dir_path])
                )

            directory_list = set(entries.keys())

            if not directory_list:
                continue

            dates = {name: e["date"] for name, e in entries.items()}
            sizes = {name: e["size"] for name, e in entries.items() if e["size"] is not None}

            html_bytes = Handler.render_html(
                directory_list, path=dir_path, dates=dates, sizes=sizes
            ).encode("utf-8")

            artifact = _save_artifact(html_bytes, self.pulp_domain)

            page = MavenIndexPage(
                path=dir_path,
                sha256=artifact.sha256,
                _pulp_domain=self.pulp_domain,
            )
            try:
                with transaction.atomic():
                    page.save()
            except IntegrityError:
                page = MavenIndexPage.objects.get(
                    path=dir_path,
                    sha256=artifact.sha256,
                    _pulp_domain=self.pulp_domain,
                )

            try:
                with transaction.atomic():
                    ContentArtifact.objects.create(
                        artifact=artifact,
                        content=page,
                        relative_path=f"{dir_path}index.html",
                    )
            except IntegrityError:
                pass

            new_version.add_content(MavenIndexPage.objects.filter(pk=page.pk))

    class Meta:
        default_related_name = "%(app_label)s_%(model_name)s"
        permissions = [  # noqa: RUF012
            ("modify_mavenrepository", "Can modify content in Maven repository"),
            ("manage_roles_mavenrepository", "Can manage roles on Maven repository"),
            ("repair_mavenrepository", "Can repair Maven repository metadata"),
        ]
