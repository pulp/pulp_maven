"""Exercise the real repository lifecycle, SQL extraction, and directory updates."""

from collections import Counter
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from pulpcore.plugin.models import ContentArtifact, Domain
from pulpcore.plugin.util import get_domain, set_domain

from pulp_maven.app.models import (
    MavenArtifact,
    MavenDirectory,
    MavenDirectoryState,
    MavenIndexPage,
    MavenRepository,
)
from pulp_maven.app.tasks import _save_artifact

pytestmark = pytest.mark.django_db


@pytest.fixture
def repository(settings, monkeypatch):
    default, _ = Domain.objects.get_or_create(
        name="default",
        defaults={
            "storage_class": settings.STORAGES["default"]["BACKEND"],
        },
    )
    set_domain(default)
    # Isolate index/HTML work from the independent Maven metadata and Bloom costs.
    for name in ("_ensure_packages", "_generate_metadata", "_generate_bloom_filter"):
        monkeypatch.setattr(MavenRepository, name, lambda *args: None)
    return MavenRepository.objects.create(
        name=str(uuid4()), pulp_labels={"incremental_index_pages": "true"}
    )


def content(path, payload=None):
    artifact = _save_artifact(payload or path.encode(), get_domain())
    unit = MavenArtifact.init_from_artifact_and_relative_path(artifact, path)
    unit.save()
    ContentArtifact.objects.create(content=unit, artifact=artifact, relative_path=path)
    return unit


def change(repository, add=(), remove=()):
    with repository.new_version() as version:
        version.remove_content(MavenArtifact.objects.filter(pk__in=[u.pk for u in remove]))
        version.add_content(MavenArtifact.objects.filter(pk__in=[u.pk for u in add]))
    return version


class QueryStats:
    def __init__(self):
        self.total = 0
        self.kinds = Counter()

    def __call__(self, execute, sql, params, many, context):
        self.total += 1
        operation = sql.split(None, 1)[0]
        table = next(
            (
                name
                for name in (
                    "maven_mavenindexpage",
                    "maven_mavendirectorychild",
                    "maven_mavendirectory",
                    "core_contentartifact",
                )
                if f'"{name}"' in sql
            ),
            "other",
        )
        self.kinds[operation, table] += 1
        return execute(sql, params, many, context)


def test_incremental_html_reuses_ancestor_pages(repository):
    a = content("com/example/lib/1.0/a.jar")
    b = content("com/example/lib/1.0/b.jar")
    first = change(repository, [a])
    before = dict(
        MavenDirectory.objects.filter(repository=repository).values_list("path", "page_id")
    )
    with CaptureQueriesContext(connection) as queries:
        second = change(repository, [b])
    after = dict(
        MavenDirectory.objects.filter(repository=repository).values_list("path", "page_id")
    )
    assert after[""] == before[""]
    assert after["com/example/lib/"] == before["com/example/lib/"]
    assert after["com/example/lib/1.0/"] != before["com/example/lib/1.0/"]
    old_page = MavenIndexPage.objects.get(pk=before["com/example/lib/1.0/"])
    assert first.content.filter(pk=old_page.pk).exists()
    assert not second.content.filter(pk=old_page.pk).exists()
    listing = ContentArtifact.objects.get(content_id=after["com/example/lib/1.0/"])
    with listing.artifact.file.open("rb") as stream:
        assert b"a.jar" in (body := stream.read()) and b"b.jar" in body
    assert not any("LIKE" in q["sql"] and "core_contentartifact" in q["sql"] for q in queries)
    # All artifact reads in the generator are restricted to affected paths.
    artifact_selects = [
        q["sql"]
        for q in queries
        if q["sql"].startswith("SELECT") and 'FROM "core_contentartifact"' in q["sql"]
    ]
    assert all(
        "relative_path" in sql and (" IN " in sql or "LIMIT" in sql) for sql in artifact_selects
    )


def test_last_child_removal_prunes_pages_and_directories(repository):
    a = content("com/example/lib/1.0/a.jar")
    change(repository, [a])
    final = change(repository, remove=[a])
    assert not MavenDirectory.objects.filter(repository=repository).exists()
    assert not final.content.filter(pulp_type="maven.index-page").exists()


def test_failed_version_summary_recovers(repository):
    a = content("com/example/lib/1.0/a.jar")
    b = content("org/example/lib/1.0/b.jar")
    first = change(repository, [a])
    with patch(
        "pulpcore.plugin.models.RepositoryVersion._compute_counts", side_effect=RuntimeError("fail")
    ):
        with pytest.raises(RuntimeError):
            change(repository, [b])
    pending = MavenDirectoryState.objects.get(repository=repository).version_id
    assert pending is not None
    assert not repository.versions.filter(pk=pending, complete=True).exists()
    final = change(repository, remove=[a])
    assert final.complete
    assert not MavenDirectory.objects.filter(repository=repository).exists()
    assert not first.content.filter(pk=b.pk).exists()


def test_noop_preserves_summary_cursor(repository):
    a = content("com/example/lib/1.0/a.jar")
    first = change(repository, [a])
    noop = change(repository, [a])
    assert not repository.versions.filter(pk=noop.pk).exists()
    state = MavenDirectoryState.objects.get(repository=repository)
    assert state.version_id == first.pk


def test_modify_batches_orphans_and_survives_retention(repository):
    from pulpcore.plugin.tasking import add_and_remove

    repository.retain_repo_versions = 1
    repository.save()
    units = [content(f"com/example/lib/1.0/file{i}.jar") for i in range(200)]
    add_and_remove(repository.pk, [u.pk for u in units], [])
    first = repository.latest_version()
    assert first.content.filter(pulp_type="maven.artifact").count() == 200
    before = dict(MavenDirectory.objects.values_list("path", "page_id"))
    add_and_remove(repository.pk, [], [units[0].pk])
    latest = repository.latest_version()
    assert not repository.versions.filter(pk=first.pk).exists()
    assert latest.content.filter(pk=before[""]).exists()
    assert latest.content.filter(pulp_type="maven.artifact").count() == 199


def test_disabled_then_enabled_rebuilds_summary(repository):
    change(repository, [content("com/example/lib/1.0/a.jar")])
    repository.pulp_labels = {}
    repository.save()
    change(repository, [content("org/example/lib/1.0/b.jar")])
    repository.pulp_labels = {"incremental_index_pages": "true"}
    repository.save()
    final = change(repository, [content("com/example/lib/1.0/c.jar")])
    assert MavenDirectory.objects.filter(repository=repository, path="org/").exists()
    assert final.content.filter(pulp_type="maven.index-page").count() == 9


def test_explicit_repair_rebuilds_derived_rows(repository):
    unit = content("com/example/lib/1.0/a.jar")
    change(repository, [unit])
    MavenDirectory.objects.filter(repository=repository).delete()
    with repository.new_version() as version:
        repository._generate_index_pages(version, affected_paths={""})
    assert MavenDirectory.objects.filter(repository=repository).count() == 5


def test_base_version_switch_restores_pages(repository):
    from pulpcore.plugin.tasking import add_and_remove

    a = content("com/example/lib/1.0/a.jar")
    b = content("org/example/lib/1.0/b.jar")
    first = change(repository, [a])
    change(repository, [b], [a])
    add_and_remove(repository.pk, [], [], base_version_pk=first.pk)
    final = repository.latest_version()
    assert final.content.filter(pk=a.pk).exists()
    assert not final.content.filter(pk=b.pk).exists()
    assert not MavenDirectory.objects.filter(path__startswith="org/").exists()


def test_same_directory_changes_batch_queries(repository):
    seed = content("com/example/lib/1.0/seed.jar")
    change(repository, [seed])
    units = [content(f"com/example/lib/1.0/file{i}.jar") for i in range(100)]

    additions = QueryStats()
    with connection.execute_wrapper(additions):
        change(repository, units)
    assert additions.kinds["DELETE", "maven_mavendirectorychild"] == 1
    assert additions.kinds["SELECT", "maven_mavendirectorychild"] <= 2
    assert additions.total < 100

    removals = QueryStats()
    with connection.execute_wrapper(removals):
        change(repository, remove=units)
    assert removals.kinds["DELETE", "maven_mavendirectorychild"] == 1
    assert removals.kinds["SELECT", "maven_mavendirectorychild"] <= 2
    assert removals.total < 100


def test_many_directory_changes_batch_queries(repository):
    seed = content("org/example/seed/1.0/seed.jar")
    change(repository, [seed])
    units = [content(f"org/example/group{i}/1.0/file.jar") for i in range(25)]

    queries = QueryStats()
    with connection.execute_wrapper(queries):
        change(repository, units)

    assert queries.kinds["SELECT", "maven_mavendirectory"] <= 3
    assert queries.kinds["INSERT", "maven_mavendirectory"] == 1
    assert queries.kinds["SELECT", "maven_mavendirectorychild"] <= 2
    assert queries.kinds["INSERT", "maven_mavendirectorychild"] <= 2
    assert queries.kinds["INSERT", "core_contentartifact"] <= 2
    assert queries.total < 400
