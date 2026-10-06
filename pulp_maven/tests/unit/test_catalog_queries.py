"""SQL shape of the package index. These call catalog helpers, not the HTTP API."""

import re
from uuid import uuid4

import pytest
from django.db import connection

from pulpcore.plugin.models import Domain, RepositoryContent
from pulpcore.plugin.util import set_domain

from pulp_maven.app.catalog import (
    assemble_package_index,
    distinct_ga_qs,
    maven_packages_in_version,
    order_flat_packages,
)
from pulp_maven.app.models import MavenPackage, MavenRepository

pytestmark = pytest.mark.django_db

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _bound_id_count(sql):
    """How many ids the statement binds, whether mogrified or left as ``%s``."""
    lowered = sql.lower()
    return max(len(_UUID_RE.findall(lowered)), lowered.count("%s"))


@pytest.fixture
def repository(settings):
    default, _ = Domain.objects.get_or_create(
        name="default",
        defaults={"storage_class": settings.STORAGES["default"]["BACKEND"]},
    )
    set_domain(default)
    return MavenRepository.objects.create(name=str(uuid4()))


def test_flat_package_order_is_byte_order(repository):
    """Punctuation stays significant: ``5.3.18.rhlw-00003`` before ``5.3.180``."""
    uid = uuid4().hex[:8]
    group_id = f"com.example.{uid}"
    gavs = [
        (group_id, "hello", "5.3.180"),
        (group_id, "hello", "5.3.18.rhlw-00003"),
        (group_id, "hello", "5.3.18"),
        (group_id, "hello", "1.0.0"),
    ]
    units = [
        MavenPackage.objects.create(group_id=group, artifact_id=artifact, version=version)
        for group, artifact, version in gavs
    ]
    with repository.new_version() as repo_version:
        repo_version.add_content(MavenPackage.objects.filter(pk__in=[unit.pk for unit in units]))

    versions = list(
        order_flat_packages(maven_packages_in_version(repo_version)).values_list(
            "version", flat=True
        )
    )
    assert versions == ["1.0.0", "5.3.18", "5.3.18.rhlw-00003", "5.3.180"]


def test_package_list_sql_does_not_expand_content_ids(repository):
    """The package index must filter through RepositoryContent, not content_ids.

    A paged list used to inline every content UUID in the version. Default
    ordering must also skip the last_updated aggregate. Sorting by last_updated
    still computes that aggregate, and still must not inline the UUID list.
    """
    uid = uuid4().hex[:8]
    example = f"com.example.{uid}"
    extra = f"com.extra.{uid}"
    gavs = [
        (example, "hello", "5.3.18"),
        (example, "hello", "5.3.18.rhlw-00003"),
        (example, "hello", "5.3.180"),
        (example, "hello", "1.0.0"),
        (example, "world", "1.0.0"),
        (f"org.other.{uid}", "widget", "1.0.0"),
    ]
    gavs += [(extra, f"lib{i:02d}", "1.0.0") for i in range(34)]
    units = [
        MavenPackage.objects.create(group_id=group_id, artifact_id=artifact_id, version=version)
        for group_id, artifact_id, version in gavs
    ]
    with repository.new_version() as repo_version:
        repo_version.add_content(MavenPackage.objects.filter(pk__in=[unit.pk for unit in units]))

    content_count = RepositoryContent.objects.filter(
        repository_id=repository.pk, version_removed__isnull=True
    ).count()
    content_qs = maven_packages_in_version(repo_version)
    default_qs = distinct_ga_qs(content_qs, repo_version, ordering=("group_id", "artifact_id"))
    updated_qs = distinct_ga_qs(
        content_qs,
        repo_version,
        ordering=("-last_updated", "group_id", "artifact_id"),
    )
    count_qs = content_qs.order_by().values("group_id", "artifact_id").distinct()

    connection.force_debug_cursor = True
    start = len(connection.queries)
    try:
        count_qs.count()
        page = list(default_qs[:20])
        list(updated_qs[:20])
        assemble_start = len(connection.queries)
        rows = assemble_package_index(content_qs, page, repo_version)
    finally:
        connection.force_debug_cursor = False
    captured = [query["sql"] for query in connection.queries[start:assemble_start]]
    assembled = [query["sql"] for query in connection.queries[assemble_start:]]

    assert rows
    assert content_count >= 6
    assert len(captured) == 3, captured
    count_sql, default_sql, updated_sql = captured
    for sql in captured + assembled:
        lowered = sql.lower()
        assert "core_repositorycontent" in lowered, sql
        assert "content_id" in lowered, sql
        assert _bound_id_count(sql) < content_count, sql
    assert "max(" not in count_sql.lower()
    assert "max(" not in default_sql.lower()
    assert "distinct" in default_sql.lower()
    assert "max(" in updated_sql.lower()
    # MAX((SELECT ... content_ptr_id)) is rejected by Postgres: the correlated
    # column is not in GROUP BY. repository_id must be on the membership join.
    lowered_updated = updated_sql.lower()
    assert "max((select" not in lowered_updated
    assert 'in_repo."repository_id"' in lowered_updated
    assert "limit" in default_sql.lower()
    assert "limit" in updated_sql.lower()
    assert any("distinct" in sql.lower() for sql in assembled)
