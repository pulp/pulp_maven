import datetime
import hashlib
import logging
import os
import tempfile
from collections import defaultdict
from xml.etree.ElementTree import Element, SubElement, tostring

from asgiref.sync import sync_to_async
from django.core.files import File as DjangoFile

from pulpcore.plugin.models import (
    Content,
    ContentArtifact,
    PublishedMetadata,
    RemoteArtifact,
    RepositoryVersion,
)
from pulpcore.plugin.tasking import add_and_remove

from pulp_maven.app.models import MavenArtifact, MavenPublication, MavenRemote, MavenRepository

log = logging.getLogger(__name__)


async def aadd_and_remove(*args, **kwargs):
    return await sync_to_async(add_and_remove)(*args, **kwargs)


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
