import datetime
import hashlib
import logging
import tempfile
from collections import defaultdict
from xml.etree.ElementTree import Element, SubElement, tostring

from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction
from django.db.models import Q

from pulpcore.plugin.models import (
    Artifact,
    Content,
    ContentArtifact,
    RemoteArtifact,
)
from pulpcore.plugin.tasking import add_and_remove

from pulp_maven.app.models import (
    MavenArtifact,
    MavenMetadata,
    MavenRemote,
    MavenRepository,
)

log = logging.getLogger(__name__)

METADATA_FILENAMES = [
    "maven-metadata.xml",
    "maven-metadata.xml.md5",
    "maven-metadata.xml.sha1",
    "maven-metadata.xml.sha256",
]
PREFIXES_TXT_FILENAME = ".meta/prefixes.txt"


def _compute_prefix(group_id):
    """Return the repository prefix for a Maven group ID.

    The prefix is the first two slash-separated segments of the group path,
    or the full path if the group ID has fewer than two segments.

    Examples:
        com.fasterxml.jackson.core -> /com/fasterxml
        com.springframework.boot   -> /com/springframework
        commons-fileupload         -> /commons-fileupload
    """
    group_path = group_id.replace(".", "/")
    segments = group_path.split("/")
    if len(segments) >= 2:
        return f"/{segments[0]}/{segments[1]}"
    return f"/{group_path}"


def _save_prefixes_txt(prefixes, pulp_domain):
    """Build .meta/prefixes.txt content, save as MavenMetadata, return list of PKs."""
    # This magic header is a format marker. First line MUST be exactly this string.
    # Maven clients look for this exact string to confirm the file is a valid
    # prefixes list (not an HTML error page, etc).
    # "2.0" is the format version from the Sonatype spec.
    # The "##" prefix means it's treated as a comment/header by parsers, not as
    # a prefix entry.
    lines = ["## repository-prefixes/2.0"] + sorted(prefixes)
    content_bytes = ("\n".join(lines) + "\n").encode("utf-8")

    artifact = _save_artifact(content_bytes, pulp_domain)

    metadata_content = MavenMetadata(
        group_id="",
        artifact_id="",
        version=None,
        filename=PREFIXES_TXT_FILENAME,
        sha256=artifact.sha256,
        _pulp_domain=pulp_domain,
    )

    try:
        with transaction.atomic():
            metadata_content.save()
    except IntegrityError:
        metadata_content = MavenMetadata.objects.get(
            group_id="",
            artifact_id="",
            version=None,
            filename=PREFIXES_TXT_FILENAME,
            sha256=artifact.sha256,
            _pulp_domain=pulp_domain,
        )

    try:
        with transaction.atomic():
            ContentArtifact.objects.create(
                artifact=artifact,
                content=metadata_content,
                relative_path=PREFIXES_TXT_FILENAME,
            )
    except IntegrityError:
        pass

    return [metadata_content.pk]


def _save_artifact(content_bytes, pulp_domain):
    """Write bytes to a temp file and create a Pulp Artifact via init_and_validate."""
    with tempfile.NamedTemporaryFile() as tmp:
        tmp.write(content_bytes)
        tmp.flush()
        artifact = Artifact.init_and_validate(tmp.name)
        artifact.pulp_domain = pulp_domain
        try:
            with transaction.atomic():
                artifact.save()
        except IntegrityError:
            artifact = Artifact.objects.get(
                sha256=artifact.sha256,
                pulp_domain=pulp_domain,
            )
    return artifact


def _save_artifacts_batch(pages, pulp_domain):
    """Save a collection of (dir_path, html_bytes) pairs as Artifacts.

    Follows the same pattern as pulpcore's sync pipeline ArtifactSaver stage:
    batch-query which sha256 digests already exist, then only open temp files
    for genuinely new content.  This avoids the file-descriptor accumulation that
    occurs when checking existence one-at-a-time in a tight loop — existing
    artifacts are returned with zero file opens.

    Args:
        pages: iterable of (dir_path, html_bytes) pairs.
        pulp_domain: the Domain instance to scope Artifacts to.

    Returns:
        dict mapping dir_path -> Artifact.
    """
    # Map each unique sha256 to one representative (dir_path, html_bytes).
    # Multiple directories can share the same sha256 (identical listings).
    sha256_to_repr: dict = {}
    dir_to_sha256: dict = {}
    for dir_path, html_bytes in pages:
        digest = hashlib.sha256(html_bytes).hexdigest()
        dir_to_sha256[dir_path] = digest
        if digest not in sha256_to_repr:
            sha256_to_repr[digest] = (dir_path, html_bytes)

    # Batch-check existence — one DB query per 1 000 sha256s to keep
    # the IN clause at a manageable size.
    sha256_to_artifact: dict = {}
    all_digests = list(sha256_to_repr.keys())
    for i in range(0, len(all_digests), 1000):
        for artifact in Artifact.objects.filter(
            sha256__in=all_digests[i : i + 1000],
            pulp_domain=pulp_domain,
        ):
            sha256_to_artifact[artifact.sha256] = artifact

    # Upload only the artifacts that do not already exist, in parallel.
    # _save_artifact is thread-safe (transaction.atomic() with IntegrityError handling).
    # Each worker opens at most two fds (NamedTemporaryFile + init_and_validate),
    # both closed before the call returns, so there is no fd accumulation.
    new_items = [
        (digest, html_bytes)
        for digest, (_, html_bytes) in sha256_to_repr.items()
        if digest not in sha256_to_artifact
    ]

    if new_items:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _upload(digest, html_bytes):
            return digest, _save_artifact(html_bytes, pulp_domain)

        with ThreadPoolExecutor(max_workers=20) as pool:
            futs = {pool.submit(_upload, d, h): d for d, h in new_items}
            for fut in as_completed(futs):
                digest, artifact = fut.result()
                sha256_to_artifact[digest] = artifact

    return {dir_path: sha256_to_artifact[digest] for dir_path, digest in dir_to_sha256.items()}


async def aadd_and_remove(*args, **kwargs):
    return await sync_to_async(add_and_remove)(*args, **kwargs)


def _add_and_remove_skip_metadata(*args, **kwargs):
    """Wrapper around add_and_remove that skips metadata generation."""
    from pulp_maven.app.models import _pull_through_ctx

    _pull_through_ctx.active = True
    try:
        return add_and_remove(*args, **kwargs)
    finally:
        _pull_through_ctx.active = False


async def pull_through_aadd_and_remove(*args, **kwargs):
    """Async add_and_remove that skips metadata generation for pull-through content."""
    return await sync_to_async(_add_and_remove_skip_metadata)(*args, **kwargs)


def add_cached_content_to_repository(repository_pk=None, remote_pk=None):
    """
    Create a new repository version by adding content that was cached by pulpcore-content when
    streaming it from a remote.

    Args:
        repository_pk (uuid): The primary key for a Repository for which a new Repository Version
            should be created.
        remote_pk (uuid): The primary key for a Remote which will be used to identify Content
            created by pulpcore-content when it streamed it to clients.
    """
    repository = MavenRepository.objects.get(pk=repository_pk)
    remote = MavenRemote.objects.get(pk=remote_pk)

    latest_version = repository.latest_version()

    if latest_version.number == 0:
        date_min = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    else:
        date_min = latest_version.pulp_created
    with repository.new_version(base_version=None) as new_version:
        ca_id_list = RemoteArtifact.objects.filter(
            remote=remote, pulp_created__gte=date_min
        ).values_list("content_artifact")
        content_list = ContentArtifact.objects.filter(pk__in=ca_id_list).values_list("content")
        new_version.add_content(Content.objects.filter(pk__in=content_list))


def repair_metadata(repository_pk):
    """
    Regenerate all maven-metadata.xml and checksum files for a repository.

    Scans every MavenArtifact in the latest version, groups them by
    (group_id, artifact_id), and creates a new repository version with
    fresh metadata replacing any stale entries.

    Args:
        repository_pk (str): Primary key of the MavenRepository.
    """
    repository = MavenRepository.objects.get(pk=repository_pk)
    latest_version = repository.latest_version()

    if not latest_version:
        return

    all_pairs = defaultdict(set)
    for row in (
        MavenArtifact.objects.filter(pk__in=latest_version.content)
        .values("group_id", "artifact_id", "version")
        .distinct()
    ):
        all_pairs[(row["group_id"], row["artifact_id"])].add(row["version"])

    has_metadata = MavenMetadata.objects.filter(
        pk__in=latest_version.content,
        filename__in=METADATA_FILENAMES,
    ).exists()
    has_prefixes_txt = MavenMetadata.objects.filter(
        pk__in=latest_version.content,
        filename=PREFIXES_TXT_FILENAME,
    ).exists()

    if not all_pairs and not has_metadata and not has_prefixes_txt:
        return

    from pulp_maven.app.models import _pull_through_ctx

    _pull_through_ctx.active = True
    try:
        with repository.new_version() as new_version:
            new_metadata_pks = []

            for (group_id, artifact_id), version_set in all_pairs.items():
                versions = sorted(version_set)
                if not versions:
                    continue
                new_metadata_pks.extend(
                    _create_metadata_content(
                        group_id, artifact_id, versions, repository.pulp_domain
                    )
                )

            for (group_id, artifact_id), version_set in all_pairs.items():
                for version in sorted(version_set):
                    if not version.endswith("-SNAPSHOT"):
                        continue
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
                            group_id,
                            artifact_id,
                            version,
                            filenames,
                            repository.pulp_domain,
                        )
                    )

            new_pks = set(new_metadata_pks)

            # Remove metadata for (group_id, artifact_id) pairs that no
            # longer have any MavenArtifact in the repository, regardless
            # of the metadata's version field.
            orphaned = MavenMetadata.objects.filter(
                pk__in=new_version.content,
                filename__in=METADATA_FILENAMES,
            )
            if all_pairs:
                valid_pairs_q = Q()
                for group_id, artifact_id in all_pairs:
                    valid_pairs_q |= Q(group_id=group_id, artifact_id=artifact_id)
                orphaned = orphaned.exclude(valid_pairs_q)
            new_version.remove_content(orphaned)

            # Remove stale repo-level metadata for active pairs.
            stale = MavenMetadata.objects.filter(
                pk__in=new_version.content,
                version=None,
                filename__in=METADATA_FILENAMES,
            ).exclude(pk__in=new_pks)
            new_version.remove_content(stale)

            # Remove stale version-level SNAPSHOT metadata for active pairs.
            stale_version = MavenMetadata.objects.filter(
                pk__in=new_version.content,
                version__endswith="-SNAPSHOT",
                filename__in=METADATA_FILENAMES,
            ).exclude(pk__in=new_pks)
            new_version.remove_content(stale_version)

            already_present = set(
                new_version.content.filter(pk__in=new_pks).values_list("pk", flat=True)
            )
            pks_to_add = new_pks - already_present
            if pks_to_add:
                new_version.add_content(MavenMetadata.objects.filter(pk__in=pks_to_add))

            # --- prefixes.txt ---
            # Remove any existing prefixes.txt (may be stale or orphaned)
            stale_prefixes_pks = list(
                MavenMetadata.objects.filter(
                    pk__in=new_version.content,
                    filename=PREFIXES_TXT_FILENAME,
                ).values_list("pk", flat=True)
            )
            if stale_prefixes_pks:
                new_version.remove_content(MavenMetadata.objects.filter(pk__in=stale_prefixes_pks))

            # Rebuild from all group_ids in the repository.
            all_group_ids = {gid for gid, _ in all_pairs}
            all_prefixes = {_compute_prefix(gid) for gid in all_group_ids}
            if all_prefixes:
                prefixes_pks = _save_prefixes_txt(all_prefixes, repository.pulp_domain)
                already_present = set(
                    new_version.content.filter(pk__in=prefixes_pks).values_list("pk", flat=True)
                )
                pks_to_add = set(prefixes_pks) - already_present
                if pks_to_add:
                    new_version.add_content(MavenMetadata.objects.filter(pk__in=pks_to_add))
    finally:
        _pull_through_ctx.active = False

    log.info(
        "Repaired metadata: repository=%s, pairs=%d",
        repository.name,
        len(all_pairs),
    )


def repair_index_pages(repository_pk):
    """Generate (or regenerate) HTML index pages for every directory in the latest version.

    Scans every non-index ContentArtifact path in the latest repository version, derives
    the full set of ancestor directory paths, and creates a new repository version with
    fresh ``MavenIndexPage`` content units for all of them — replacing any stale pages.

    This is a one-time catch-up for repositories created before the pre-generation feature
    was deployed.  After this task completes every directory URL in the repository will be
    served by a pre-generated page rather than the on-demand fallback.

    Args:
        repository_pk (str): Primary key of the MavenRepository.
    """
    from pulp_maven.app.models import MavenIndexPage, _pull_through_ctx

    repository = MavenRepository.objects.get(pk=repository_pk)
    latest_version = repository.latest_version()

    if not latest_version:
        return

    # Derive all ancestor directory paths from every non-index ContentArtifact.
    all_paths = set()
    for (relative_path,) in (
        ContentArtifact.objects.filter(
            content__in=latest_version.content,
        )
        .exclude(
            content__pulp_type="maven.index-page",
        )
        .values_list("relative_path")
    ):
        parts = relative_path.split("/")
        for i in range(len(parts)):
            all_paths.add("" if i == 0 else "/".join(parts[:i]) + "/")

    if not all_paths:
        log.info(
            "repair_index_pages: repository=%s has no content — nothing to do",
            repository.name,
        )
        return

    _pull_through_ctx.active = True
    try:
        with repository.new_version() as new_version:
            # Drop every existing index page so we start clean.
            stale = MavenIndexPage.objects.filter(pk__in=new_version.content)
            if stale.exists():
                new_version.remove_content(stale)

            # Regenerate pages for every directory using the shared implementation.
            repository._generate_index_pages(new_version, affected_paths=all_paths)
    finally:
        _pull_through_ctx.active = False

    log.info(
        "Repaired index pages: repository=%s, directories=%d",
        repository.name,
        len(all_paths),
    )


def _save_metadata_content(group_id, artifact_id, version, base_path, metadata_xml, pulp_domain):
    """Save maven-metadata.xml and checksum files as MavenMetadata content.

    Returns a list of MavenMetadata primary keys.
    """
    xml_artifact = _save_artifact(metadata_xml, pulp_domain)

    files_to_create = [("maven-metadata.xml", base_path, xml_artifact)]
    for ext, algo in [(".md5", "md5"), (".sha1", "sha1"), (".sha256", "sha256")]:
        checksum_value = getattr(xml_artifact, algo)
        if checksum_value is None:
            checksum_value = hashlib.new(algo, metadata_xml).hexdigest()
        checksum_bytes = checksum_value.encode("utf-8")
        checksum_artifact = _save_artifact(checksum_bytes, pulp_domain)
        files_to_create.append((f"maven-metadata.xml{ext}", f"{base_path}{ext}", checksum_artifact))

    pks = []
    for filename, relative_path, artifact in files_to_create:
        metadata_content = MavenMetadata(
            group_id=group_id,
            artifact_id=artifact_id,
            version=version,
            filename=filename,
            sha256=artifact.sha256,
            _pulp_domain=pulp_domain,
        )
        try:
            with transaction.atomic():
                metadata_content.save()
        except IntegrityError:
            metadata_content = MavenMetadata.objects.get(
                group_id=group_id,
                artifact_id=artifact_id,
                version=version,
                filename=filename,
                sha256=artifact.sha256,
                _pulp_domain=pulp_domain,
            )

        try:
            with transaction.atomic():
                ContentArtifact.objects.create(
                    artifact=artifact,
                    content=metadata_content,
                    relative_path=relative_path,
                )
        except IntegrityError:
            pass

        pks.append(metadata_content.pk)

    return pks


def _create_metadata_content(group_id, artifact_id, versions, pulp_domain):
    """Build repo-level maven-metadata.xml and checksums for a (group_id, artifact_id) pair.

    Returns a list of MavenMetadata primary keys.
    """
    metadata_xml = _build_maven_metadata_xml(group_id, artifact_id, versions)
    group_path = group_id.replace(".", "/")
    base_path = f"{group_path}/{artifact_id}/maven-metadata.xml"
    return _save_metadata_content(group_id, artifact_id, None, base_path, metadata_xml, pulp_domain)


def _build_maven_metadata_xml(group_id, artifact_id, versions):
    """Build a maven-metadata.xml listing all versions for an artifact."""
    root = Element("metadata")
    SubElement(root, "groupId").text = group_id
    SubElement(root, "artifactId").text = artifact_id

    versioning = SubElement(root, "versioning")
    latest = versions[-1] if versions else ""
    release = next((v for v in reversed(versions) if not v.endswith("-SNAPSHOT")), "")
    SubElement(versioning, "latest").text = latest
    SubElement(versioning, "release").text = release

    versions_elem = SubElement(versioning, "versions")
    for v in versions:
        SubElement(versions_elem, "version").text = v

    now = datetime.datetime.now(datetime.timezone.utc)
    SubElement(versioning, "lastUpdated").text = now.strftime("%Y%m%d%H%M%S")

    return (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        + tostring(root, encoding="unicode").encode("utf-8")
        + b"\n"
    )


def _parse_extension_and_classifier(filename, artifact_id, version):
    """Extract extension and optional classifier from a Maven filename."""
    prefix = f"{artifact_id}-{version}"
    if not filename.startswith(prefix):
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        return ext, None
    remainder = filename[len(prefix) :]
    if remainder.startswith("-"):
        remainder = remainder[1:]
        if "." in remainder:
            classifier, extension = remainder.split(".", 1)
        else:
            classifier = remainder
            extension = ""
        return extension, classifier
    elif remainder.startswith("."):
        return remainder[1:], None
    else:
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        return ext, None


def _build_version_level_metadata_xml(group_id, artifact_id, version, filenames):
    """Build a version-level maven-metadata.xml for a SNAPSHOT version."""
    root = Element("metadata")
    SubElement(root, "groupId").text = group_id
    SubElement(root, "artifactId").text = artifact_id
    SubElement(root, "version").text = version

    versioning = SubElement(root, "versioning")

    snapshot = SubElement(versioning, "snapshot")
    SubElement(snapshot, "localCopy").text = "true"

    now = datetime.datetime.now(datetime.timezone.utc)
    updated = now.strftime("%Y%m%d%H%M%S")

    sv_list = SubElement(versioning, "snapshotVersions")
    for fname in sorted(filenames):
        extension, classifier = _parse_extension_and_classifier(fname, artifact_id, version)
        sv = SubElement(sv_list, "snapshotVersion")
        if classifier:
            SubElement(sv, "classifier").text = classifier
        SubElement(sv, "extension").text = extension
        SubElement(sv, "value").text = version
        SubElement(sv, "updated").text = updated

    SubElement(versioning, "lastUpdated").text = updated

    return (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        + tostring(root, encoding="unicode").encode("utf-8")
        + b"\n"
    )


def _create_version_level_metadata_content(group_id, artifact_id, version, filenames, pulp_domain):
    """Build version-level maven-metadata.xml and checksums for a SNAPSHOT version.

    Returns a list of MavenMetadata primary keys.
    """
    metadata_xml = _build_version_level_metadata_xml(group_id, artifact_id, version, filenames)
    group_path = group_id.replace(".", "/")
    base_path = f"{group_path}/{artifact_id}/{version}/maven-metadata.xml"
    return _save_metadata_content(
        group_id, artifact_id, version, base_path, metadata_xml, pulp_domain
    )
