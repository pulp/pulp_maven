"""Helpers for repository package catalog, metrics, and rebuild collapse."""

from collections import defaultdict

from django.db.models import CharField, Func, Max, Min, Q, Value
from django.db.models.functions import Coalesce

from pulp_maven.app.models import MavenPackage
from pulp_maven.app.versions import (
    DEFAULT_PACKAGE_INDEX_ORDERING,
    parse_package_search,
    rebuild_release,
    version_sort_key,
)

# POSIX regex for REGEXP_REPLACE. PostgreSQL does not treat ``\d`` as digits.
BUILD_SUFFIX_PG_REGEX = r"\.[a-zA-Z]+-[0-9]+$"


def base_version_annotation(field_name="version"):
    """SQL expression that strips a trailing rebuild suffix from ``version``.

    PostgreSQL POSIX regex does not treat ``\\d`` as digits, so the SQL pattern
    uses ``[0-9]`` while the Python pattern in ``strip_build_suffix`` uses ``\\d``.
    Implemented with ``REGEXP_REPLACE`` so it does not depend on Django's
    ``RegexpReplace`` (not present in every Django 4.2/5.2 packaging Pulp uses).
    """
    return Func(
        field_name,
        Value(BUILD_SUFFIX_PG_REGEX),
        Value(""),
        function="REGEXP_REPLACE",
        output_field=CharField(),
    )


def collapse_maven_builds(queryset):
    """Keep one MavenPackage per ``(group_id, artifact_id, base_version)``.

    ``base_version`` is ``version`` with a trailing rebuild suffix stripped.
    The unit with the latest ``pulp_created`` is kept.
    """
    return (
        queryset.prefetch_related(None)
        .annotate(_collapse_base_version=base_version_annotation())
        .order_by("group_id", "artifact_id", "_collapse_base_version", "-pulp_created")
        .distinct("group_id", "artifact_id", "_collapse_base_version")
    )


def maven_packages_in_version(repository_version):
    """MavenPackage content contained in ``repository_version``."""
    if repository_version is None:
        return MavenPackage.objects.none()
    return MavenPackage.objects.filter(pk__in=repository_version.content)


def apply_package_prefix_filters(queryset, group_id_prefix=None, artifact_id_prefix=None):
    """Apply case-insensitive prefix filters used by the package index."""
    if group_id_prefix:
        queryset = queryset.filter(group_id__istartswith=group_id_prefix)
    if artifact_id_prefix:
        queryset = queryset.filter(artifact_id__istartswith=artifact_id_prefix)
    return queryset


def apply_package_search_filter(queryset, search=None):
    """Apply case-insensitive ``search`` used by the package index.

    Combines with other filters using AND. A term without ``:`` is
    ``group_id`` contains OR ``artifact_id`` contains. A term with ``:`` is
    ``group_id`` contains the left part AND ``artifact_id`` contains the right.
    """
    parsed = parse_package_search(search)
    if parsed is None:
        return queryset
    if parsed[0] == "or":
        term = parsed[1]
        return queryset.filter(Q(group_id__icontains=term) | Q(artifact_id__icontains=term))
    _, group_term, artifact_term = parsed
    q = Q()
    if group_term:
        q &= Q(group_id__icontains=group_term)
    if artifact_term:
        q &= Q(artifact_id__icontains=artifact_term)
    return queryset.filter(q)


def membership_in_version_q(repository, repository_version):
    """Q-object matching RepositoryContent rows present in ``repository_version``."""
    return Q(
        version_memberships__repository=repository,
        version_memberships__version_added__number__lte=repository_version.number,
    ) & (
        Q(version_memberships__version_removed__isnull=True)
        | Q(version_memberships__version_removed__number__gt=repository_version.number)
    )


def last_updated_annotation(repository, repository_version):
    """Newest repository-membership time among all MavenPackage units for a GA.

    Uses ``RepositoryContent.pulp_created`` (any rebuild), falling back to the
    content unit's ``pulp_created``.
    """
    return Coalesce(
        Max(
            "version_memberships__pulp_created",
            filter=membership_in_version_q(repository, repository_version),
        ),
        Max("pulp_created"),
    )


def distinct_ga_qs(content_qs, repository, repository_version, ordering=None):
    """One row per distinct ``(group_id, artifact_id)``, ordered for stable pagination."""
    if ordering is None:
        ordering = DEFAULT_PACKAGE_INDEX_ORDERING
    qs = content_qs.order_by().values("group_id", "artifact_id")
    if repository_version is None:
        qs = qs.annotate(last_updated=Max("pulp_created"))
    else:
        qs = qs.annotate(last_updated=last_updated_annotation(repository, repository_version))
    return qs.order_by(*ordering)


def assemble_package_index(content_qs, ga_rows, repository, repository_version):
    """Build package-index dicts for ``ga_rows``.

    Each row is one ``(group_id, artifact_id)``. ``versions`` are distinct base
    versions, newest first (numeric-token order). ``latest_releases`` keeps the
    newest rebuild (latest ``pulp_created``) per base version in the same order. ``created_at`` is that unit's
    repository-membership time (``RepositoryContent.pulp_created``), falling
    back to the content unit's ``pulp_created``. ``last_updated`` is the newest
    membership time among all units for the GA (any rebuild), taken from
    ``ga_rows`` when annotated.
    """
    if not ga_rows or repository_version is None:
        return []

    pair_q = Q()
    for row in ga_rows:
        pair_q |= Q(group_id=row["group_id"], artifact_id=row["artifact_id"])

    in_this_version = membership_in_version_q(repository, repository_version)

    newest_units = list(
        content_qs.filter(pair_q)
        .prefetch_related(None)
        .annotate(_base_version=base_version_annotation())
        .order_by("group_id", "artifact_id", "_base_version", "-pulp_created")
        .distinct("group_id", "artifact_id", "_base_version")
    )
    newest = [
        {
            "pk": unit.pk,
            "group_id": unit.group_id,
            "artifact_id": unit.artifact_id,
            "version": unit.version,
            "_base_version": unit._base_version,
            "pulp_created": unit.pulp_created,
        }
        for unit in newest_units
    ]

    memberships = {}
    if newest:
        memberships = dict(
            MavenPackage.objects.filter(pk__in=[row["pk"] for row in newest])
            .annotate(
                membership_created=Min(
                    "version_memberships__pulp_created",
                    filter=in_this_version,
                )
            )
            .values_list("pk", "membership_created")
        )

    releases_by_ga = defaultdict(list)
    for row in newest:
        releases_by_ga[(row["group_id"], row["artifact_id"])].append(row)

    result = []
    for row in ga_rows:
        ga = (row["group_id"], row["artifact_id"])
        rels = sorted(
            releases_by_ga.get(ga, []),
            key=lambda item: version_sort_key(item["_base_version"]),
            reverse=True,
        )
        versions = [item["_base_version"] for item in rels]
        latest_releases = [
            {
                "version": item["_base_version"],
                "release": rebuild_release(item["version"]),
                "created_at": memberships.get(item["pk"]) or item["pulp_created"],
            }
            for item in rels
        ]
        result.append(
            {
                "group_id": row["group_id"],
                "artifact_id": row["artifact_id"],
                "last_updated": row.get("last_updated"),
                "versions": versions,
                "latest_releases": latest_releases,
            }
        )
    return result


def repository_metrics(content_qs):
    """Distinct package / logical-version / build counts for MavenPackage.

    Identity is always ``MavenPackage`` (POM-backed GAV), never MavenArtifact:

    * ``package_count``: distinct ``(group_id, artifact_id)``
    * ``version_count``: distinct ``(group_id, artifact_id, base_version)``
      after rebuild-suffix strip
    * ``build_count``: distinct ``(group_id, artifact_id, version)`` (full GAV)
    """
    content_qs = content_qs.order_by()
    return {
        "package_count": content_qs.values("group_id", "artifact_id").distinct().count(),
        "version_count": (
            content_qs.annotate(_base_version=base_version_annotation())
            .values("group_id", "artifact_id", "_base_version")
            .distinct()
            .count()
        ),
        "build_count": content_qs.values("group_id", "artifact_id", "version").distinct().count(),
    }
