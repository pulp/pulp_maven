import datetime
import hashlib
import logging
import os
import tempfile
from collections import defaultdict
from xml.etree.ElementTree import Element, SubElement, tostring

from asgiref.sync import sync_to_async
from django.core.files import File as DjangoFile
from django.db import IntegrityError, transaction

from pulpcore.plugin.models import (
    Artifact,
    Content,
    ContentArtifact,
    PublishedMetadata,
    RemoteArtifact,
    RepositoryVersion,
)
from pulpcore.plugin.tasking import add_and_remove

from pulp_maven.app.models import (
    MavenArtifact,
    MavenMetadata,
    MavenPublication,
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


def publish(repository_version_pk):
    """
    Create a MavenPublication based on a RepositoryVersion.

    Generates maven-metadata.xml for each (group_id, artifact_id) pair and checksum files
    (.md5, .sha1, .sha256) for the generated metadata.

    Args:
        repository_version_pk (str): The primary key of the RepositoryVersion to publish.
    """
    repo_version = RepositoryVersion.objects.get(pk=repository_version_pk)

    log.info(
        "Publishing: repository=%s, version=%s",
        repo_version.repository.name,
        repo_version.number,
    )

    with tempfile.TemporaryDirectory(dir=".") as working_dir:
        with MavenPublication.create(repo_version, pass_through=True) as publication:
            artifacts = MavenArtifact.objects.filter(
                pk__in=repo_version.content.values_list("pk", flat=True)
            )

            groups = defaultdict(set)
            for artifact in artifacts.iterator():
                groups[(artifact.group_id, artifact.artifact_id)].add(artifact.version)

            for (group_id, artifact_id), versions in groups.items():
                versions = sorted(versions)
                metadata_xml = _build_maven_metadata_xml(group_id, artifact_id, versions)
                group_path = group_id.replace(".", "/")
                relative_path = f"{group_path}/{artifact_id}/maven-metadata.xml"

                file_path = os.path.join(working_dir, "maven-metadata.xml")
                with open(file_path, "wb") as f:
                    f.write(metadata_xml)

                with open(file_path, "rb") as f:
                    pm = PublishedMetadata.create_from_file(
                        file=DjangoFile(f),
                        publication=publication,
                        relative_path=relative_path,
                    )

                _create_checksum_files(publication, relative_path, pm, metadata_xml, working_dir)

    log.info("Publication: %s created", publication.pk)


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

    if not all_pairs:
        return

    with repository.new_version() as new_version:
        stale = MavenMetadata.objects.filter(
            pk__in=new_version.content,
            version=None,
            filename__in=METADATA_FILENAMES,
        )
        new_version.remove_content(stale)

        new_metadata_pks = []

        for (group_id, artifact_id), version_set in all_pairs.items():
            versions = sorted(version_set)
            if not versions:
                continue
            new_metadata_pks.extend(
                _create_metadata_content(group_id, artifact_id, versions, repository.pulp_domain)
            )

        if new_metadata_pks:
            new_version.add_content(MavenMetadata.objects.filter(pk__in=new_metadata_pks))

    log.info(
        "Repaired metadata: repository=%s, pairs=%d",
        repository.name,
        len(all_pairs),
    )


def _create_metadata_content(group_id, artifact_id, versions, pulp_domain):
    """
    Build maven-metadata.xml and checksum files for a single (group_id, artifact_id) pair.

    Returns a list of MavenMetadata primary keys for the created content.
    """
    metadata_xml = _build_maven_metadata_xml(group_id, artifact_id, versions)
    group_path = group_id.replace(".", "/")
    base_path = f"{group_path}/{artifact_id}/maven-metadata.xml"

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
            version=None,
            filename=filename,
            sha256=artifact.sha256,
            _pulp_domain=pulp_domain,
        )
        try:
            with transaction.atomic():
                metadata_content.save()
        except IntegrityError:
            metadata_content = MavenMetadata.objects.get(sha256=artifact.sha256)

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


def _create_checksum_files(
    publication, relative_path, published_metadata, content_bytes, working_dir
):
    """Create .md5, .sha1, .sha256 checksum files using stored digests from the Artifact."""
    artifact = published_metadata.contentartifact_set.select_related("artifact").get().artifact

    for ext, attr in [(".md5", "md5"), (".sha1", "sha1"), (".sha256", "sha256")]:
        digest = getattr(artifact, attr, None)
        if not digest:
            digest = hashlib.new(attr, content_bytes).hexdigest()

        checksum_relative_path = f"{relative_path}{ext}"
        file_path = os.path.join(working_dir, f"checksum{ext}")
        with open(file_path, "w") as f:
            f.write(digest)

        with open(file_path, "rb") as f:
            PublishedMetadata.create_from_file(
                file=DjangoFile(f),
                publication=publication,
                relative_path=checksum_relative_path,
            )
