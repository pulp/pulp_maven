"""Set-based version finalization: duplicate removal, packages, metadata and bulk content."""

from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from pulpcore.plugin.models import ContentArtifact, Domain
from pulpcore.plugin.util import get_domain, set_domain

from pulp_maven.app.models import (
    MavenArtifact,
    MavenIndexPage,
    MavenMetadata,
    MavenPackage,
    MavenRepository,
)
from pulp_maven.app.tasks import PREFIXES_TXT_FILENAME, _save_artifact
from pulp_maven.app.version_utils import _duplicates_impossible, bulk_create_content

pytestmark = pytest.mark.django_db

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>{group_id}</groupId><artifactId>{artifact_id}</artifactId><version>{version}</version>
  <name>{artifact_id} library</name>
</project>"""


@pytest.fixture
def domain(settings):
    default, _ = Domain.objects.get_or_create(
        name="default", defaults={"storage_class": settings.STORAGES["default"]["BACKEND"]}
    )
    set_domain(default)
    return default


@pytest.fixture
def repository(domain, monkeypatch):
    for name in ("_generate_index_pages", "_generate_bloom_filter"):
        monkeypatch.setattr(MavenRepository, name, lambda *args: None)
    return MavenRepository.objects.create(name=str(uuid4()))


def artifact_unit(path, payload=None):
    artifact = _save_artifact(payload or path.encode(), get_domain())
    unit = MavenArtifact.init_from_artifact_and_relative_path(artifact, path)
    unit.save()
    ContentArtifact.objects.create(content=unit, artifact=artifact, relative_path=path)
    return unit


def gav_units(group_id, artifact_id, version):
    base = f"{group_id.replace('.', '/')}/{artifact_id}/{version}/{artifact_id}-{version}"
    pom = POM.format(group_id=group_id, artifact_id=artifact_id, version=version)
    return [
        artifact_unit(f"{base}.jar", f"jar {group_id} {artifact_id} {version}".encode()),
        artifact_unit(f"{base}.pom", pom.encode()),
    ]


def metadata_unit(version, sha256):
    unit = MavenMetadata(
        group_id="org.example",
        artifact_id="lib",
        version=version,
        filename="maven-metadata.xml",
        sha256=sha256,
    )
    unit.save()
    return unit


def change(repository, add=(), remove=()):
    with repository.new_version() as version:
        if remove:
            version.remove_content(MavenArtifact.objects.filter(pk__in=[u.pk for u in remove]))
        if add:
            version.add_content(MavenArtifact.objects.filter(pk__in=[u.pk for u in add]))
    return version


def test_duplicates_are_impossible_only_for_types_keyed_by_their_unique_constraint():
    assert _duplicates_impossible(MavenArtifact)
    assert _duplicates_impossible(MavenPackage)
    assert not _duplicates_impossible(MavenMetadata)
    assert not _duplicates_impossible(MavenIndexPage)


def test_remove_duplicates_replaces_units_with_the_same_key(repository, monkeypatch):
    for name in ("_ensure_packages", "_generate_metadata"):
        monkeypatch.setattr(MavenRepository, name, lambda *args: None)
    repo_level = metadata_unit(None, "1" * 64)
    snapshot = metadata_unit("1.0-SNAPSHOT", "2" * 64)
    release = metadata_unit("1.0", "3" * 64)
    with repository.new_version() as version:
        version.add_content(
            MavenMetadata.objects.filter(pk__in=[repo_level.pk, snapshot.pk, release.pk])
        )

    new_repo_level = metadata_unit(None, "4" * 64)
    new_snapshot = metadata_unit("1.0-SNAPSHOT", "5" * 64)
    with repository.new_version() as version:
        version.add_content(
            MavenMetadata.objects.filter(pk__in=[new_repo_level.pk, new_snapshot.pk])
        )

    assert set(version.content.values_list("pk", flat=True)) == {
        new_repo_level.pk,
        new_snapshot.pk,
        release.pk,
    }


def test_bulk_create_content_saves_master_and_detail_rows(domain):
    existing = MavenPackage(group_id="org.example", artifact_id="lib", version="1.0")
    existing.save()
    units = [
        MavenPackage(group_id="org.example", artifact_id="lib", version=version, name=version)
        for version in ("1.0", "2.0", "3.0")
    ]

    saved = bulk_create_content(units)

    assert saved[0].pk == existing.pk
    assert [unit.version for unit in saved] == ["1.0", "2.0", "3.0"]
    created = MavenPackage.objects.filter(pk__in=[unit.pk for unit in saved[1:]])
    assert sorted(created.values_list("version", "name", "pulp_type")) == [
        ("2.0", "2.0", MavenPackage.get_pulp_type()),
        ("3.0", "3.0", MavenPackage.get_pulp_type()),
    ]
    assert all(not unit._state.adding for unit in saved)


def finalize_queries(repository, units):
    with CaptureQueriesContext(connection) as queries:
        change(repository, add=units)
    return len(queries)


def test_finalize_query_count_does_not_grow_with_the_number_of_gavs(repository):
    few = [unit for v in range(2) for unit in gav_units("org.few", "lib", f"1.{v}")]
    many = [
        unit
        for a in range(4)
        for v in range(5)
        for unit in gav_units("org.many", f"lib{a}", f"1.{v}")
    ]
    finalize_queries(repository, gav_units("org.warmup", "lib", "1.0"))

    assert finalize_queries(repository, many) == finalize_queries(repository, few)


def test_finalize_creates_packages_and_metadata(repository):
    first = gav_units("org.example", "lib", "1.0") + gav_units("org.example", "lib", "2.0")
    change(repository, add=first)
    version = change(repository, add=gav_units("com.other", "tool", "1.0-SNAPSHOT"))

    packages = MavenPackage.objects.filter(pk__in=version.content)
    assert sorted(packages.values_list("group_id", "artifact_id", "version", "name")) == [
        ("com.other", "tool", "1.0-SNAPSHOT", "tool library"),
        ("org.example", "lib", "1.0", "lib library"),
        ("org.example", "lib", "2.0", "lib library"),
    ]
    metadata = MavenMetadata.objects.filter(pk__in=version.content)
    assert set(metadata.values_list("group_id", "artifact_id", "version", "filename")) == {
        ("", "", None, PREFIXES_TXT_FILENAME),
        *[("com.other", "tool", None, f) for f in sorted(FILES)],
        *[("com.other", "tool", "1.0-SNAPSHOT", f) for f in sorted(FILES)],
        *[("org.example", "lib", None, f) for f in sorted(FILES)],
    }
    paths = dict(
        ContentArtifact.objects.filter(content__in=metadata).values_list(
            "relative_path", "artifact__file"
        )
    )
    assert "org/example/lib/maven-metadata.xml.sha1" in paths
    assert (
        read(paths[PREFIXES_TXT_FILENAME])
        == "## repository-prefixes/2.0\n/com/other\n/org/example\n"
    )
    assert "<version>2.0</version>" in read(paths["org/example/lib/maven-metadata.xml"])

    version = change(repository, remove=first)
    assert not MavenPackage.objects.filter(pk__in=version.content, group_id="org.example").exists()
    assert not MavenMetadata.objects.filter(pk__in=version.content, group_id="org.example").exists()
    prefixes = ContentArtifact.objects.get(
        content__in=version.content, relative_path=PREFIXES_TXT_FILENAME
    )
    assert read(prefixes.artifact.file.name) == "## repository-prefixes/2.0\n/com/other\n"


FILES = {
    "maven-metadata.xml",
    "maven-metadata.xml.md5",
    "maven-metadata.xml.sha1",
    "maven-metadata.xml.sha256",
}


def read(name):
    with get_domain().get_storage().open(name) as file:
        return file.read().decode()
