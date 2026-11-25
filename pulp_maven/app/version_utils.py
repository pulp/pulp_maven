"""Set-based helpers for finalizing versions of large repositories.

``pk__in=version.content`` is an ``unnest`` of the version's ``content_ids`` array, which
PostgreSQL estimates at a handful of rows. Combined with a filter on many coordinates, the planner
then checks every unit of the version against every coordinate, which takes minutes on
repositories with a million units. The helpers here express version membership through
``RepositoryContent`` instead, keep coordinates in correlated subqueries rather than giant ``OR``
filters, and create content in batches rather than one unit (four statements) at a time.
"""

import itertools

from django.db import IntegrityError, connection, transaction
from django.db.models import Exists, OuterRef, Q, UniqueConstraint
from django.db.models.expressions import RawSQL

from pulpcore.plugin.models import Content, RepositoryContent
from pulpcore.plugin.repo_version_utils import remove_duplicates as core_remove_duplicates

# PostgreSQL accepts at most 65535 bind parameters per statement.
MAX_QUERY_PARAMS = 65535


def present_in(new_version, exclude_added=False):
    """Return an ``Exists`` filter for content that is currently in an unfinished ``new_version``.

    While a version is being built it is the repository's latest, so its content is exactly the
    repository's relations that have not been removed. The planner can drive this from a small
    candidate set through the ``(repository, content)`` index, or hash it for a large one.

    Args:
        new_version: the version being finalized.
        exclude_added: only match content that was already in the version this one is based on.
    """
    relations = RepositoryContent.objects.filter(
        repository_id=new_version.repository_id,
        content_id=OuterRef("pk"),
        version_removed__isnull=True,
    )
    if exclude_added:
        relations = relations.exclude(version_added=new_version)
    return Exists(relations)


def changed_in(new_version, model, added=True, removed=True):
    """Return the ``model`` units that ``new_version`` added and/or removed (in one subquery)."""
    condition = Q()
    if added:
        condition |= Q(version_added=new_version)
    if removed:
        condition |= Q(version_removed=new_version)
    relations = RepositoryContent.objects.filter(condition, repository_id=new_version.repository_id)
    return model.objects.filter(pk__in=relations.values("content_id"))


def pk_queryset(model, pks):
    """Return ``model`` units with the given primary keys, passed as a single array parameter."""
    return model.objects.filter(pk__in=RawSQL("SELECT unnest(%s::uuid[])", [list(pks)]))


def remove_duplicates(new_version):
    """Remove units that share ``repo_key_fields`` with units added to ``new_version``.

    Same result as pulpcore's ``remove_duplicates``, which ORs 1,000 added keys at a time and
    filters the whole version with each batch. Here each content type is one query that starts
    from the added units, and types whose unique constraint already rules out duplicates within a
    domain are skipped.
    """
    if new_version.base_version_id:
        # Rare: the "existing" content is the base version rather than the previous one.
        core_remove_duplicates(new_version)
        return

    repository = new_version.repository.cast()
    added = new_version.added()
    previously_present = present_in(new_version, exclude_added=True)
    duplicates = []
    for model in repository.CONTENT_TYPES:
        keys = model.repo_key_fields
        if not keys or _duplicates_impossible(model):
            continue
        added_units = model.objects.filter(pk__in=added.filter(pulp_type=model.get_pulp_type()))
        if not added_units.exists():
            continue
        # NULL never equals NULL in SQL, so match each combination of NULL key fields separately.
        nullable = [key for key in keys if model._meta.get_field(key).null]
        for null_flags in itertools.product((False, True), repeat=len(nullable)):
            null_keys = {key for key, is_null in zip(nullable, null_flags) if is_null}
            isnull = {f"{key}__isnull": True for key in null_keys}
            same_key = added_units.filter(
                **isnull, **{key: OuterRef(key) for key in keys if key not in null_keys}
            )
            duplicates.extend(
                model.objects.filter(Exists(same_key), previously_present, **isnull).values_list(
                    "pk", flat=True
                )
            )
    if duplicates:
        new_version.remove_content(pk_queryset(Content, duplicates))


def _duplicates_impossible(model):
    """Whether a unique constraint guarantees no two units in a domain share ``repo_key_fields``.

    A repository version only holds content from one domain, so if some unique constraint covers
    only key fields and the domain, two distinct units can never be repository duplicates.
    """
    keys = {*model.repo_key_fields, "_pulp_domain", "pulp_domain"}
    unique_sets = [set(fields) for fields in model._meta.unique_together]
    unique_sets += [
        set(constraint.fields)
        for constraint in model._meta.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.fields
        and constraint.condition is None
    ]
    return any(fields <= keys for fields in unique_sets)


def bulk_create_content(units):
    """Save new units of one Content detail model with two multi-row INSERTs per batch.

    Django's ``bulk_create`` refuses multi-table models, so pulpcore saves content one unit at a
    time (four statements each). Content primary keys are generated client-side, so the master and
    detail rows can be inserted in bulk. If a batch collides with existing rows (another task
    created the same unit), that batch falls back to get-or-create per unit.

    Returns:
        The saved units, with existing rows in place of colliding ones.
    """
    units = list(units)
    if not units:
        return []
    model = type(units[0])
    master_fields = Content._meta.local_concrete_fields
    detail_fields = model._meta.local_concrete_fields
    batch_size = MAX_QUERY_PARAMS // max(len(master_fields), len(detail_fields))
    saved = []
    for start in range(0, len(units), batch_size):
        batch = units[start : start + batch_size]
        for unit in batch:
            unit.content_ptr_id = unit.pulp_id
        try:
            with transaction.atomic():
                _insert_rows(Content, master_fields, batch)
                _insert_rows(model, detail_fields, batch)
        except IntegrityError:
            for index, unit in enumerate(batch):
                try:
                    with transaction.atomic():
                        unit.save()
                except IntegrityError:
                    batch[index] = model.objects.get(unit.q())
        else:
            for unit in batch:
                unit._state.adding = False
                unit._state.db = connection.alias
        saved.extend(batch)
    return saved


def _insert_rows(model, fields, units):
    values = []
    for unit in units:
        values.extend(
            field.get_db_prep_save(field.pre_save(unit, add=True), connection) for field in fields
        )
    quote = connection.ops.quote_name
    row = f"({', '.join(['%s'] * len(fields))})"
    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {quote(model._meta.db_table)} "
            f"({', '.join(quote(field.column) for field in fields)}) "
            f"VALUES {', '.join([row] * len(units))}",
            values,
        )
