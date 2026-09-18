"""Publish to S3 before core completes a version or removes its predecessor."""

from pulpcore.plugin.models import RepositoryVersion

from .config import enabled, store
from .extract import batches, changed_paths, check_directories, checked_rows, entries, files
from .format import InvalidIndex, artifact_entries
from .state import INFO_KEY, attach, read
from .store import NeedsCompaction


def baseline_entries(repository, version):
    """Stream a full build, proving coverage before any manifest is published."""
    for rows in batches(files(version).iterator(chunk_size=1000)):
        check_directories(version, [row["relative_path"] for row in rows])
        yield from entries(checked_rows(rows, repository.pulp_domain))


def changed_entries(repository, version):
    for paths in batches(changed_paths(version).iterator(chunk_size=1000)):
        check_directories(version, paths)
        yield from entries(checked_rows(files(version, paths), repository.pulp_domain))


def deleted_hashes(version):
    # Delete every changed path first. The engine gives surviving upserts priority,
    # including same-path replacements and index.html directory aliases.
    for path in changed_paths(version).iterator(chunk_size=1000):
        for entry in artifact_entries(path, "00" * 32, 0, 0):
            yield entry.path_hash


def finalize(repository, version):
    if not enabled(repository):
        return
    if not version.added().exists() and not version.removed().exists():
        return  # Core deletes a no-op version. Do not publish an unreachable index.
    previous = (
        RepositoryVersion.objects.filter(
            repository=repository, complete=True, number__lt=version.number
        )
        .only("pk", "info")
        .order_by("-number")
        .first()
    )
    index = store(repository)
    if previous is None or INFO_KEY not in previous.info:
        manifest = index.create(str(version.pk), baseline_entries(repository, version))
    else:
        # Missing/corrupt storage is a failure, never an empty predecessor.
        manifest = read(index, previous, repository)
        if any(a.level == b.level for a, b in zip(manifest.segments[1:], manifest.segments[2:])):
            manifest = index.compact(manifest)
        try:
            manifest = index.update(
                str(version.pk),
                manifest,
                changed_entries(repository, version),
                deleted_hashes(version),
            )
        except NeedsCompaction:
            # Rare full rewrite at the segment cap. Explicit maintenance can do
            # this earlier; both run under the repository reservation.
            manifest = index.compact(manifest, rebase=True)
            manifest = index.update(
                str(version.pk),
                manifest,
                changed_entries(repository, version),
                deleted_hashes(version),
            )
    if manifest.version_id != str(version.pk):
        raise InvalidIndex("Publication returned the wrong version")
    # No DB save here: core saves this together with complete=True. An exception
    # above aborts modify before retention; DB failure below leaves unreferenced S3 data.
    attach(version, repository, manifest)
