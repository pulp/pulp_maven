import datetime
import hashlib
import logging
import os
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.etree.ElementTree import Element, SubElement, tostring

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files import File
from django.db import IntegrityError, transaction
from django.db.models import Max, Min, Q
from django.utils import timezone
from redis.exceptions import RedisError

from pulpcore.plugin.models import (
    Artifact,
    ContentArtifact,
)
from pulpcore.plugin.tasking import add_and_remove

from pulp_maven.app.bloom import (
    BLOOM_FILTER_CONFIG_LABEL,
    add_paths_to_bloom_filter,
    bloom_filter_config_key,
    bloom_filter_key,
    get_redis_connection,
    grown_bloom_filter_capacity,
    mark_bloom_filter_not_ready,
    parse_bloom_filter_config,
    redis_bloom_filter_matches_config,
    replace_bloom_filter,
)
from pulp_maven.app.models import (
    MavenArtifact,
    MavenMetadata,
    MavenPackage,
    MavenPendingDeploy,
    MavenRepository,
    _deploy_batch_ctx,
)
from pulp_maven.app.util import PulpListingParser

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


_storage_writers = None
_storage_writers_lock = threading.Lock()


def _forget_storage_writers():
    # A forked child (pulpcore runs each task in one) inherits the pool object but not its
    # threads, so work submitted to it would wait forever. The child starts its own pool.
    global _storage_writers, _storage_writers_lock
    _storage_writers = None
    _storage_writers_lock = threading.Lock()


os.register_at_fork(after_in_child=_forget_storage_writers)


def _storage_writer_pool():
    """Return this process's pool of threads that write files to storage.

    The storage backend keeps one connection per thread (an S3 client, for example), so
    threads that outlive a single call keep their connections warm. A pool created per
    call would open a new connection for every file and lose what writing in parallel
    gains.
    """
    global _storage_writers
    with _storage_writers_lock:
        if _storage_writers is None:
            _storage_writers = ThreadPoolExecutor(
                max_workers=8, thread_name_prefix="maven-storage-writer"
            )
        return _storage_writers


def _save_blobs(blobs, pulp_domain):
    """Save byte strings as Artifacts and return them keyed by sha256.

    Follows the same pattern as pulpcore's sync pipeline ArtifactSaver stage: one query
    finds which digests already exist, and only genuinely new content is written, so an
    existing artifact costs no file open and no upload.

    The new files are written to storage in parallel, each straight to the path its
    Artifact uses, so inserting the Artifact rows afterwards finds them in place and does
    not upload them again. These files are small and numerous (metadata, checksums, index
    pages), so the round trips to storage, not the bytes, are what would cost time if
    they were written one after another.

    Args:
        blobs: iterable of bytes.
        pulp_domain: the Domain instance to scope Artifacts to.

    Returns:
        dict mapping sha256 -> Artifact.
    """
    from pulpcore.plugin.util import get_domain, set_domain

    data_by_digest = {}
    for data in blobs:
        data_by_digest.setdefault(hashlib.sha256(data).hexdigest(), data)

    # One DB query per 1 000 sha256s keeps the IN clause at a manageable size.
    sha256_to_artifact = {}
    all_digests = list(data_by_digest)
    for i in range(0, len(all_digests), 1000):
        for artifact in Artifact.objects.filter(
            sha256__in=all_digests[i : i + 1000],
            pulp_domain=pulp_domain,
        ):
            sha256_to_artifact[artifact.sha256] = artifact

    new_items = [
        (digest, data)
        for digest, data in data_by_digest.items()
        if digest not in sha256_to_artifact
    ]
    if not new_items:
        return sha256_to_artifact

    # ContextVar values (including the domain set by pulpcore's task runner) do NOT
    # propagate to ThreadPoolExecutor worker threads. Capture the domain in the calling
    # thread and set it explicitly at the start of each worker. Context.run() cannot be
    # used here: a single Context object cannot be entered concurrently by multiple
    # threads (raises "cannot enter context: already entered").
    current_domain = get_domain()

    with tempfile.TemporaryDirectory() as temp_dir:

        def _store(item):
            digest, data = item
            set_domain(current_domain)
            with tempfile.NamedTemporaryFile(dir=temp_dir, delete=False) as tmp:
                tmp.write(data)
            artifact = Artifact.init_and_validate(tmp.name, expected_digests={"sha256": digest})
            artifact.pulp_domain = pulp_domain
            final_path = artifact.storage_path("")
            with open(tmp.name, "rb") as fh:
                stored_path = artifact.file.storage.save(final_path, File(fh))
            if stored_path == final_path:
                # Already in place: saving the Artifact keeps the file where it is.
                artifact.file = stored_path
            else:
                # The storage kept an existing file and chose another name. Drop the copy
                # and let saving the Artifact move the file the usual way.
                artifact.file.storage.delete(stored_path)
            return artifact

        artifacts = sorted(_storage_writer_pool().map(_store, new_items), key=lambda a: a.sha256)

        # Database insertion stays batched in the calling thread.
        for i in range(0, len(artifacts), 500):
            batch = artifacts[i : i + 500]
            try:
                for artifact in Artifact.objects.bulk_get_or_create(batch):
                    sha256_to_artifact[artifact.sha256] = artifact
            finally:
                # bulk_create bypasses Artifact.save(), including its file cleanup.
                for artifact in batch:
                    artifact.file.close()

    return sha256_to_artifact


def _save_artifacts_batch(pages, pulp_domain):
    """Save a collection of (dir_path, html_bytes) pairs as Artifacts.

    Multiple directories can share the same listing, and so the same Artifact.

    Args:
        pages: iterable of (dir_path, html_bytes) pairs.
        pulp_domain: the Domain instance to scope Artifacts to.

    Returns:
        dict mapping dir_path -> Artifact.
    """
    pages = list(pages)
    sha256_to_artifact = _save_blobs((html_bytes for _, html_bytes in pages), pulp_domain)
    return {
        dir_path: sha256_to_artifact[hashlib.sha256(html_bytes).hexdigest()]
        for dir_path, html_bytes in pages
    }


def _parse_index_pages(page_sources):
    """Return parsed entries and failures keyed by digest for index page Artifacts."""

    def parse_page(digest, artifact):
        parser = PulpListingParser()
        with artifact.file.open("rb") as handle:
            parser.feed(handle.read().decode("utf-8"))
        parser.close()
        return digest, parser.entries

    result = {}
    failures = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(parse_page, digest, artifact): digest for digest, artifact in page_sources
        }
        for future in as_completed(futures):
            digest = futures[future]
            try:
                _, entries = future.result()
            except Exception as exc:
                failures[digest] = exc
            else:
                result[digest] = entries

    return result, failures


async def aadd_and_remove(*args, **kwargs):
    return await sync_to_async(add_and_remove)(*args, **kwargs)


def commit_pending_deploys(repository_pk):
    """Add the files deployed to a repository and still pending, a batch per version.

    Waits until no file has been deployed to the repository for MAVEN_DEPLOY_BATCH_LINGER
    seconds, but not longer than MAVEN_DEPLOY_BATCH_MAX_WAIT seconds after the oldest
    pending file, then adds everything pending in one repository version. Repeats while
    files keep arriving, so nothing deployed during a commit is left behind.

    Within a batch the newest file deployed to a path wins, as it would had each file been
    added in its own version.

    Args:
        repository_pk (str): The repository to commit pending deploys for.
    """
    repository = MavenRepository.objects.get(pk=repository_pk)
    linger = datetime.timedelta(seconds=settings.MAVEN_DEPLOY_BATCH_LINGER)
    max_wait = datetime.timedelta(seconds=settings.MAVEN_DEPLOY_BATCH_MAX_WAIT)
    pending = MavenPendingDeploy.objects.filter(repository=repository)

    while True:
        span = pending.aggregate(first=Min("pulp_created"), last=Max("pulp_created"))
        if span["first"] is None:
            return
        wait = (
            min(span["last"] + linger, span["first"] + max_wait) - timezone.now()
        ).total_seconds()
        if wait > 0:
            time.sleep(wait)
            continue

        rows = list(
            pending.order_by("pulp_created").values_list("pk", "content_id", "relative_path")
        )
        newest_by_path = {}
        for _, content_id, relative_path in rows:
            newest_by_path[relative_path] = content_id
        _deploy_batch_ctx.active = True
        try:
            add_and_remove(
                repository_pk,
                add_content_units=list(newest_by_path.values()),
                remove_content_units=[],
            )
        finally:
            _deploy_batch_ctx.active = False
        MavenPendingDeploy.objects.filter(pk__in=[pk for pk, _, _ in rows]).delete()
        log.info(
            "Committed pending deploys: repository=%s, files=%d",
            repository.name,
            len(newest_by_path),
        )


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
    files = [("maven-metadata.xml", base_path, metadata_xml)]
    for ext, algo in [(".md5", "md5"), (".sha1", "sha1"), (".sha256", "sha256")]:
        checksum_bytes = hashlib.new(algo, metadata_xml).hexdigest().encode("utf-8")
        files.append((f"maven-metadata.xml{ext}", f"{base_path}{ext}", checksum_bytes))

    sha256_to_artifact = _save_blobs((data for _, _, data in files), pulp_domain)
    files_to_create = [
        (filename, relative_path, sha256_to_artifact[hashlib.sha256(data).hexdigest()])
        for filename, relative_path, data in files
    ]

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


def _generate_bloom_filter(repository, version):
    """
    Generate the Bloom filter for the repository if configured.

    The Bloom filter is stored in Redis and enabled by the label
        `pulp_maven.bloom : <est_num_items>,<false_positive_rate>`

    Rebuild the filter if it is missing, its configuration changed, or its capacity is exhausted.
    """
    parsed_config = parse_bloom_filter_config(repository)
    if parsed_config is None:
        return
    est_num_items, false_positive_rate = parsed_config
    normalized_config = f"{est_num_items},{false_positive_rate}"

    redis = get_redis_connection()
    if redis is None:
        log.warning(
            "Redis is not configured; Bloom filter for repository %s was not updated",
            repository.name,
        )
        return

    try:
        filter_matches_config = redis_bloom_filter_matches_config(
            redis, repository, normalized_config
        )
        if filter_matches_config:
            mark_bloom_filter_not_ready(redis, repository)
            num_content = ContentArtifact.objects.filter(content__in=version.content).count()
            if num_content < est_num_items:
                # Add the new content to the bloom filter
                paths = ContentArtifact.objects.filter(content__in=version.added()).values_list(
                    "relative_path", flat=True
                )
                add_paths_to_bloom_filter(redis, bloom_filter_key(repository), paths.iterator())
                redis.set(bloom_filter_config_key(repository), normalized_config)
                return

            # Grow by 50 percent so large repositories do not rebuild too frequently.
            est_num_items = grown_bloom_filter_capacity(num_content)

        if not filter_matches_config:
            num_content = ContentArtifact.objects.filter(content__in=version.content).count()
            if num_content >= est_num_items:
                est_num_items = grown_bloom_filter_capacity(num_content)

        mark_bloom_filter_not_ready(redis, repository)

        paths = ContentArtifact.objects.filter(content__in=version.content).values_list(
            "relative_path", flat=True
        )
        replace_bloom_filter(
            redis,
            repository,
            est_num_items,
            false_positive_rate,
            paths.iterator(),
        )
    except (RedisError, TypeError):
        log.warning("Unable to update Redis Bloom filter for repository %s", repository.name)
        return

    new_config = f"{est_num_items},{false_positive_rate}"
    if repository.pulp_labels[BLOOM_FILTER_CONFIG_LABEL] != new_config:
        repository.pulp_labels[BLOOM_FILTER_CONFIG_LABEL] = new_config
        repository.save(update_fields=["pulp_labels", "pulp_last_updated"], skip_hooks=True)


def generate_bloom_filter(repository_pk):
    """Task to generate the Bloom filter for a repository."""
    repository = MavenRepository.objects.get(pk=repository_pk)
    latest_version = repository.latest_version()
    if not latest_version:
        log.warning("No latest version found for repository %s", repository.name)
        return
    _generate_bloom_filter(repository, latest_version)


def repair_packages(repository_pk):
    """
    Reconcile MavenPackage membership for a repository's latest version.

    Opens a new version and calls ``MavenRepository._ensure_packages_full_scan``. Idempotent: when
    nothing changes the empty draft version is discarded, so no new version is created.
    """
    started = time.monotonic()
    repository = MavenRepository.objects.get(pk=repository_pk)
    latest_version = repository.latest_version()
    if not latest_version:
        log.info("repair_packages: repository %r has no version; nothing to do.", repository.name)
        return

    from pulp_maven.app.models import _pull_through_ctx

    version_before = latest_version.number
    pkgs_before = MavenPackage.objects.filter(pk__in=latest_version.content).count()

    # Skip finalize_new_version's auto steps so this version only touches package membership.
    _pull_through_ctx.active = True
    try:
        with repository.new_version() as new_version:
            repository._ensure_packages_full_scan(new_version)
    finally:
        _pull_through_ctx.active = False

    new_latest = repository.latest_version()
    pkgs_after = MavenPackage.objects.filter(pk__in=new_latest.content).count()
    log.info(
        "repair_packages: repository=%r version %s -> %s | MavenPackages %d -> %d (%.1fs)",
        repository.name,
        version_before,
        new_latest.number,
        pkgs_before,
        pkgs_after,
        time.monotonic() - started,
    )
