"""Helpers for repository package catalog, metrics, and rebuild collapse."""

from collections import defaultdict

from django.db.models import CharField, FilteredRelation, Func, Max, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Collate

from pulpcore.plugin.models import RepositoryContent

from pulp_maven.app.models import MavenPackage
from pulp_maven.app.versions import (
    BUILD_SUFFIX_PATTERN,
    DEFAULT_PACKAGE_INDEX_ORDERING,
    parse_package_search,
    rebuild_release,
    version_sort_key,
)


def base_version_annotation(field_name="version"):
    """SQL expression that strips a trailing rebuild suffix from ``version``.

    Uses ``BUILD_SUFFIX_PATTERN`` (POSIX-safe: ``[^.]+`` is the last segment after
    ``letters-``, so extra dotted tails are not stripped). Implemented with
    ``REGEXP_REPLACE`` so it does not depend on Django's ``RegexpReplace``
    (not in every Django 4.2/5.2 packaging).
    """
    return Func(
        field_name,
        Value(BUILD_SUFFIX_PATTERN),
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


def _in_version_bounds_q(repository_version, prefix=""):
    """Version-added/removed bounds for rows present in ``repository_version``.

    ``prefix`` is empty for ``RepositoryContent`` and ``in_repo__`` for the
    filtered membership join. Both catalog paths use this helper.
    """
    return Q(**{f"{prefix}version_added__number__lte": repository_version.number}) & (
        Q(**{f"{prefix}version_removed__isnull": True})
        | Q(**{f"{prefix}version_removed__number__gt": repository_version.number})
    )


def memberships_in_version(repository_version):
    """RepositoryContent rows contained in ``repository_version``.

    A subquery against this queryset keeps the content-id list in the database.
    ``RepositoryVersion.content`` inlines ``content_ids`` as one bound UUID per
    content unit whenever that array is shorter than 65535.
    """
    return RepositoryContent.objects.filter(
        repository_id=repository_version.repository_id,
    ).filter(_in_version_bounds_q(repository_version))


def maven_packages_in_version(repository_version):
    """MavenPackage content contained in ``repository_version``."""
    if repository_version is None:
        return MavenPackage.objects.none()
    content_ids = (
        memberships_in_version(repository_version)
        .filter(content__pulp_type=MavenPackage.get_pulp_type())
        .order_by()
        .values("content_id")
    )
    return MavenPackage.objects.filter(pk__in=content_ids)


def order_flat_packages(queryset):
    """Order a flat package list by byte value, not the database collation.

    UTF-8 collations treat punctuation as secondary, so ``5.3.180`` sorts before
    ``5.3.18.rhlw-00003``. ``COLLATE "C"`` compares bytes: ``.`` before ``0``.
    """
    return queryset.order_by(
        Collate("group_id", "C"),
        Collate("artifact_id", "C"),
        Collate("version", "C"),
    )


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


def _membership_time(repository_version, newest=True):
    """Scalar membership time for one content row in this version.

    Valid on an ungrouped queryset (``created_at``). Do not place this inside
    ``Max()``: Postgres rejects a correlated subquery that references a column
    absent from ``GROUP BY``.
    """
    direction = "-pulp_created" if newest else "pulp_created"
    return Subquery(
        memberships_in_version(repository_version)
        .filter(content_id=OuterRef("pk"))
        .order_by(direction)
        .values("pulp_created")[:1]
    )


def annotate_grouped_last_updated(queryset, repository_version):
    """One ``last_updated`` per ``(group_id, artifact_id)``.

    The join is limited to this repository. Version bounds match
    ``memberships_in_version``. Falls back to the content unit's ``pulp_created``.
    """
    if repository_version is None:
        return queryset.values("group_id", "artifact_id").annotate(last_updated=Max("pulp_created"))
    return (
        queryset.annotate(
            in_repo=FilteredRelation(
                "version_memberships",
                condition=Q(version_memberships__repository_id=repository_version.repository_id),
            )
        )
        .values("group_id", "artifact_id")
        .annotate(
            last_updated=Coalesce(
                Max(
                    "in_repo__pulp_created",
                    filter=_in_version_bounds_q(repository_version, prefix="in_repo__"),
                ),
                Max("pulp_created"),
            )
        )
    )


def _orders_by_last_updated(ordering):
    return any(term.lstrip("-") == "last_updated" for term in ordering)


def distinct_ga_qs(content_qs, repository_version, ordering=None):
    """One row per distinct ``(group_id, artifact_id)``, ordered for stable pagination.

    ``last_updated`` is aggregated here only when it is a sort key. The default
    ``group_id, artifact_id`` order would otherwise compute that aggregate for
    every package before ``LIMIT``/``OFFSET`` can apply. The page assembler fills
    ``last_updated`` for the returned rows.
    """
    if ordering is None:
        ordering = DEFAULT_PACKAGE_INDEX_ORDERING
    qs = content_qs.order_by()
    if _orders_by_last_updated(ordering):
        qs = annotate_grouped_last_updated(qs, repository_version)
    else:
        qs = qs.values("group_id", "artifact_id").distinct()
    return qs.order_by(*ordering)


def _ga_pair_q(ga_rows):
    pair_q = Q()
    for row in ga_rows:
        pair_q |= Q(group_id=row["group_id"], artifact_id=row["artifact_id"])
    return pair_q


def _last_updated_by_ga(content_qs, ga_rows, repository_version):
    """Newest membership time for each GA on this page."""
    # A long OR list stops the planner using the (group_id, artifact_id) index.
    # Past a couple hundred rows, one grouped aggregate over the version is cheaper.
    scoped = content_qs
    if len(ga_rows) <= 200:
        scoped = content_qs.filter(_ga_pair_q(ga_rows))
    annotated = annotate_grouped_last_updated(scoped.order_by(), repository_version)
    return {(row["group_id"], row["artifact_id"]): row["last_updated"] for row in annotated}


def _license_rows(licenses):
    """License objects for the flat list. Missing data is an empty list of strings."""
    if not isinstance(licenses, list):
        return []
    rows = []
    for item in licenses:
        if not isinstance(item, dict):
            continue
        rows.append({"name": item.get("name") or "", "url": item.get("url") or ""})
    return rows


def assemble_flat_packages(units, repository_version):
    """One row per MavenPackage. ``version`` is the stored string.

    ``last_updated`` is when that unit entered this repository version
    (earliest ``RepositoryContent.pulp_created`` still in the version), falling
    back to the content unit's ``pulp_created``. ``description`` is ``""`` when
    unset. ``licenses`` is ``[]`` when unset.
    """
    if not units or repository_version is None:
        return []

    memberships = dict(
        MavenPackage.objects.filter(pk__in=[unit.pk for unit in units])
        .annotate(membership_created=_membership_time(repository_version, newest=False))
        .values_list("pk", "membership_created")
    )
    return [
        {
            "group_id": unit.group_id,
            "artifact_id": unit.artifact_id,
            "version": unit.version,
            "last_updated": memberships.get(unit.pk) or unit.pulp_created,
            "description": unit.description or "",
            "licenses": _license_rows(unit.licenses),
        }
        for unit in units
    ]


def assemble_package_index(content_qs, ga_rows, repository_version):
    """Build package-index dicts for ``ga_rows``.

    Each row is one ``(group_id, artifact_id)``. ``versions`` are distinct base
    versions, newest first (numeric-token order). ``latest_releases`` keeps the
    newest rebuild (latest ``pulp_created``) per base version in the same order. ``created_at`` is that unit's
    repository-membership time (``RepositoryContent.pulp_created``), falling
    back to the content unit's ``pulp_created``. ``last_updated`` is the newest
    membership time among all units for the GA (any rebuild), taken from
    ``ga_rows`` when the page query already annotated it.
    """
    if not ga_rows or repository_version is None:
        return []

    pair_q = _ga_pair_q(ga_rows)
    if "last_updated" in ga_rows[0]:
        last_updated_by_ga = None
    else:
        last_updated_by_ga = _last_updated_by_ga(content_qs, ga_rows, repository_version)

    # values() keeps dependencies/licenses JSON off this DISTINCT ON sort.
    newest = list(
        content_qs.filter(pair_q)
        .annotate(_base_version=base_version_annotation())
        .order_by("group_id", "artifact_id", "_base_version", "-pulp_created")
        .distinct("group_id", "artifact_id", "_base_version")
        .values("pk", "group_id", "artifact_id", "version", "_base_version", "pulp_created")
    )

    memberships = {}
    if newest:
        memberships = dict(
            MavenPackage.objects.filter(pk__in=[row["pk"] for row in newest])
            .annotate(membership_created=_membership_time(repository_version, newest=False))
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
        if last_updated_by_ga is None:
            last_updated = row.get("last_updated")
        else:
            last_updated = last_updated_by_ga.get(ga)
        result.append(
            {
                "group_id": row["group_id"],
                "artifact_id": row["artifact_id"],
                "last_updated": last_updated,
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
