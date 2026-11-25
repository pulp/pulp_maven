"""Set-based version finalization: duplicate removal."""

from uuid import uuid4

import pytest

from pulpcore.plugin.models import Domain
from pulpcore.plugin.util import set_domain

from pulp_maven.app.models import (
    MavenArtifact,
    MavenIndexPage,
    MavenMetadata,
    MavenPackage,
    MavenRepository,
)
from pulp_maven.app.version_utils import _duplicates_impossible

pytestmark = pytest.mark.django_db


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
