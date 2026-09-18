"""Bounded path extraction through the public Pulp models.

Membership predicates are correlated by content ID. Delta extraction enumerates
only changed paths, including replacements supplied by surviving content units.
"""

from itertools import islice

from django.db.models import Exists, OuterRef, Q, Subquery

from pulpcore.plugin.models import Artifact, ContentArtifact, RepositoryContent

from .format import InvalidIndex, artifact_entries

BATCH_SIZE = 1000


def batches(values, size=BATCH_SIZE):
    iterator = iter(values)
    while batch := list(islice(iterator, size)):
        yield batch


def memberships(version):
    return RepositoryContent.objects.filter(
        repository_id=version.repository_id, version_added__number__lte=version.number
    ).exclude(version_removed__number__lte=version.number)


def files(version, paths=None, *, pages=True):
    active = memberships(version).filter(content_id=OuterRef("content_id"))
    query = ContentArtifact.objects.filter(Exists(active))
    if paths is not None:
        query = query.filter(relative_path__in=paths)
    if not pages:
        query = query.exclude(content__pulp_type="maven.index-page")
    return (
        query.annotate(member_created=Subquery(active.values("pulp_created")[:1]))
        .values(
            "content_id",
            "relative_path",
            "artifact__sha256",
            "artifact__size",
            "artifact__file",
            "artifact__pulp_domain_id",
            "member_created",
            "content__pulp_type",
        )
        .order_by("relative_path", "content_id")
    )


def changed_paths(version, *, pages=True):
    changed = RepositoryContent.objects.filter(repository_id=version.repository_id).filter(
        Q(version_added_id=version.pk) | Q(version_removed_id=version.pk)
    )
    query = ContentArtifact.objects.filter(content_id__in=changed.values("content_id"))
    if not pages:
        query = query.exclude(content__pulp_type="maven.index-page")
    return query.order_by("relative_path").values_list("relative_path", flat=True).distinct()


def checked_rows(rows, domain):
    previous = None
    for row in rows:
        path = row["relative_path"]
        if (
            path == previous
            or not path
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise InvalidIndex("Ambiguous or noncanonical artifact path")
        previous = path
        if not row["artifact__sha256"] or row["artifact__size"] is None:
            raise InvalidIndex("The version contains unresolved on-demand content")
        if row["artifact__pulp_domain_id"] != domain.pk:
            raise InvalidIndex("Artifact domain mismatch")
        artifact = Artifact(sha256=row["artifact__sha256"], pulp_domain=domain)
        if artifact.storage_path(None) != row["artifact__file"]:
            raise InvalidIndex("Artifact storage does not use the supported digest layout")
        yield row


def entries(rows):
    for row in rows:
        yield from artifact_entries(
            row["relative_path"],
            row["artifact__sha256"],
            row["artifact__size"],
            int(row["member_created"].timestamp()),
        )


def check_directories(version, paths):
    """Changing files must not silently invalidate authoritative directory misses."""
    required = set()
    for path in paths:
        parts = path.split("/")
        required.update("/".join(parts[:i] + ["index.html"]) for i in range(len(parts)))
    present = {row["relative_path"] for row in files(version, required)}
    for missing in required - present:
        prefix = missing[: -len("index.html")]
        # Normally only reached when the last file and its pages were removed.
        if files(version, pages=False).filter(relative_path__startswith=prefix).exists():
            raise InvalidIndex("Generated directory coverage is incomplete")
