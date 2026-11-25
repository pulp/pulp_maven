import datetime
import hashlib
import logging
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.etree.ElementTree import Element, SubElement, tostring

from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
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
    MavenRepository,
)
from pulp_maven.app.util import PulpListingParser
from pulp_maven.app.version_utils import bulk_create_content, present_in

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

    # Prepare only the artifacts that do not already exist, in parallel. Database
    # insertion remains batched in the calling thread.
    new_items = [
        (digest, html_bytes)
        for digest, (_, html_bytes) in sha256_to_repr.items()
        if digest not in sha256_to_artifact
    ]
    if new_items:
        from pulpcore.plugin.util import get_domain, set_domain

        # ContextVar values (including the domain set by pulpcore's task runner) do
        # NOT propagate to ThreadPoolExecutor worker threads.  Capture the domain in
        # the calling thread and set it explicitly at the start of each worker.
        # Context.run() cannot be used here: a single Context object cannot be
        # entered concurrently by multiple threads (raises "cannot enter context:
        # already entered" when more than one worker fires at the same time).
        current_domain = get_domain()

        pending_artifacts = []

        def flush_pending():
            pending_artifacts.sort(key=lambda artifact: artifact.sha256)
            try:
                artifacts = Artifact.objects.bulk_get_or_create(pending_artifacts)
                sha256_to_artifact.update({artifact.sha256: artifact for artifact in artifacts})
            finally:
                # bulk_create bypasses Artifact.save(), including its file cleanup.
                for artifact in pending_artifacts:
                    artifact.file.close()
                pending_artifacts.clear()

        with tempfile.TemporaryDirectory() as temp_dir:

            def _upload(digest, html_bytes):
                set_domain(current_domain)
                with tempfile.NamedTemporaryFile(dir=temp_dir, delete=False) as tmp:
                    tmp.write(html_bytes)
                    tmp.flush()
                    artifact = Artifact.init_and_validate(
                        tmp.name,
                        expected_digests={"sha256": digest},
                    )
                artifact.pulp_domain = pulp_domain
                return artifact

            with ThreadPoolExecutor(max_workers=10) as pool:
                futures = {pool.submit(_upload, digest, data) for digest, data in new_items}
                for future in as_completed(futures):
                    pending_artifacts.append(future.result())
                    if len(pending_artifacts) >= 500:
                        flush_pending()

            if pending_artifacts:
                flush_pending()

    return {dir_path: sha256_to_artifact[digest] for dir_path, digest in dir_to_sha256.items()}


def _parse_index_pages(page_sources):
    """Return parsed entries and failures keyed by digest for index page Artifacts."""
    from pulpcore.plugin.util import get_domain, set_domain

    # Artifact files resolve their storage via the current domain ContextVar, which
    # does NOT propagate to ThreadPoolExecutor workers. Without this, every read in a
    # non-default domain with its own storage hits the default domain's backend.
    current_domain = get_domain()

    def parse_page(digest, artifact):
        set_domain(current_domain)
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
            entries = [
                _metadata_entry(group_id, artifact_id, sorted(version_set))
                for (group_id, artifact_id), version_set in all_pairs.items()
                if version_set
            ]
            snapshot_filenames = defaultdict(list)
            for group_id, artifact_id, version, filename in (
                MavenArtifact.objects.filter(
                    pk__in=latest_version.content, version__endswith="-SNAPSHOT"
                )
                .values_list("group_id", "artifact_id", "version", "filename")
                .iterator()
            ):
                snapshot_filenames[(group_id, artifact_id, version)].append(filename)
            entries.extend(
                _version_level_metadata_entry(*gav, filenames)
                for gav, filenames in snapshot_filenames.items()
            )
            new_metadata_pks = _save_metadata_batch(entries, repository.pulp_domain)

            new_pks = set(new_metadata_pks)

            # Remove metadata for (group_id, artifact_id) pairs that no
            # longer have any MavenArtifact in the repository, regardless
            # of the metadata's version field.
            orphaned = MavenMetadata.objects.filter(
                pk__in=new_version.content,
                filename__in=METADATA_FILENAMES,
            ).exclude(
                Exists(
                    MavenArtifact.objects.filter(
                        present_in(new_version),
                        group_id=OuterRef("group_id"),
                        artifact_id=OuterRef("artifact_id"),
                    )
                )
            )
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


def _metadata_files(group_id, artifact_id, version, base_path, metadata_xml):
    """Yield (coordinates, filename, relative_path, bytes) for a metadata file and its checksums."""
    coordinates = (group_id, artifact_id, version)
    yield coordinates, "maven-metadata.xml", base_path, metadata_xml
    for algorithm in ("md5", "sha1", "sha256"):
        checksum = hashlib.new(algorithm, metadata_xml).hexdigest().encode("utf-8")
        yield coordinates, f"maven-metadata.xml.{algorithm}", f"{base_path}.{algorithm}", checksum


def _save_metadata_batch(entries, pulp_domain):
    """Save maven-metadata.xml files and their checksums as MavenMetadata content in bulk.

    Args:
        entries: iterable of (group_id, artifact_id, version, base_path, metadata_xml).
        pulp_domain: the Domain to create the content in.

    Returns:
        A list of MavenMetadata primary keys, existing units included.
    """
    files = [file for entry in entries for file in _metadata_files(*entry)]
    pks = []
    for start in range(0, len(files), 4000):
        chunk = files[start : start + 4000]
        artifacts = _save_artifacts_batch(((path, data) for _, _, path, data in chunk), pulp_domain)
        sha256s = list({artifact.sha256 for artifact in artifacts.values()})
        existing = {
            (group_id, artifact_id, version, filename, sha256): pk
            for group_id, artifact_id, version, filename, sha256, pk in MavenMetadata.objects.filter(
                _pulp_domain=pulp_domain, sha256__in=sha256s
            ).values_list("group_id", "artifact_id", "version", "filename", "sha256", "pk")
        }
        units = {}
        for (group_id, artifact_id, version), filename, path, _ in chunk:
            key = (group_id, artifact_id, version, filename, artifacts[path].sha256)
            if key not in existing and key not in units:
                units[key] = MavenMetadata(
                    group_id=group_id,
                    artifact_id=artifact_id,
                    version=version,
                    filename=filename,
                    sha256=key[-1],
                    _pulp_domain=pulp_domain,
                    pulp_domain=pulp_domain,
                )
        for key, unit in zip(units, bulk_create_content(units.values())):
            existing[key] = unit.pk

        content_artifacts = {}
        for (group_id, artifact_id, version), filename, path, _ in chunk:
            pk = existing[(group_id, artifact_id, version, filename, artifacts[path].sha256)]
            content_artifacts[(pk, path)] = ContentArtifact(
                artifact=artifacts[path], content_id=pk, relative_path=path
            )
            pks.append(pk)
        for content_id, path in ContentArtifact.objects.filter(
            content_id__in={pk for pk, _ in content_artifacts}
        ).values_list("content_id", "relative_path"):
            content_artifacts.pop((content_id, path), None)
        # A stable insert order avoids deadlocks with concurrent tasks. ContentArtifact.sort_key
        # would fetch each unit's content, so sort on the (content, path) key instead.
        ContentArtifact.objects.bulk_get_or_create(
            content_artifacts[key] for key in sorted(content_artifacts, key=str)
        )
    return pks


def _metadata_entry(group_id, artifact_id, versions):
    """Return the _save_metadata_batch entry for a repo-level maven-metadata.xml."""
    metadata_xml = _build_maven_metadata_xml(group_id, artifact_id, versions)
    base_path = f"{group_id.replace('.', '/')}/{artifact_id}/maven-metadata.xml"
    return group_id, artifact_id, None, base_path, metadata_xml


def _version_level_metadata_entry(group_id, artifact_id, version, filenames):
    """Return the _save_metadata_batch entry for a SNAPSHOT version's maven-metadata.xml."""
    metadata_xml = _build_version_level_metadata_xml(group_id, artifact_id, version, filenames)
    base_path = f"{group_id.replace('.', '/')}/{artifact_id}/{version}/maven-metadata.xml"
    return group_id, artifact_id, version, base_path, metadata_xml


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
