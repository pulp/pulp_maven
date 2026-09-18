"""Maintain immediate children and render only changed directory pages."""

import hashlib
from collections import OrderedDict
from itertools import islice

from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Subquery
from django.db.models.functions import Length

from pulpcore.plugin.content import Handler
from pulpcore.plugin.models import ContentArtifact, RepositoryContent, RepositoryVersion

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
            "artifact__size",
            "member_created",
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


def previous_version(version):
    return (
        RepositoryVersion.objects.filter(
            repository_id=version.repository_id, complete=True, number__lt=version.number
        )
        .only("pk")
        .order_by("-number")
        .first()
    )


def generate(repository, version, *, rebuild=False):
    from pulp_maven.app.models import (
        MavenDirectory,
        MavenDirectoryChild,
        MavenDirectoryState,
        MavenIndexPage,
    )
    from pulp_maven.app.tasks import _save_artifacts_batch

    previous = previous_version(version)
    with transaction.atomic():
        state, _ = MavenDirectoryState.objects.select_for_update().get_or_create(
            repository=repository
        )
        rebuild = rebuild or previous is None or state.version_id != previous.pk
        directories = MavenDirectory.objects.filter(repository=repository)
        directory_cache = OrderedDict()
        removed_pages = set()

        def mark_dirty(obj):
            if not obj.dirty:
                directories.filter(pk=obj.pk).update(dirty=True)
                obj.dirty = True

        def directory(path):
            digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
            if digest in directory_cache:
                directory_cache.move_to_end(digest)
                return directory_cache[digest]
            obj, created = MavenDirectory.objects.get_or_create(
                repository=repository, path_hash=digest, defaults={"path": path}
            )
            if obj.path != path:
                raise ValueError("Directory hash collision")
            if created:
                if path:
                    parent_path, _, name = path.rstrip("/").rpartition("/")
                    parent = directory(parent_path + "/" if parent_path else "")
                    MavenDirectoryChild.objects.create(directory=parent, name=name + "/")
                    mark_dirty(parent)
            directory_cache[digest] = obj
            if len(directory_cache) > 4096:
                directory_cache.popitem(last=False)
            return obj

        def replace(rows):
            seen = set()
            pending = []
            for row in rows:
                path = row["relative_path"]
                if path in seen or not path or any(x in {"", ".", ".."} for x in path.split("/")):
                    raise ValueError("Ambiguous or noncanonical listing path")
                seen.add(path)
                parent, _, name = path.rpartition("/")
                obj = directory(parent + "/" if parent else "")
                mark_dirty(obj)
                pending.append(
                    MavenDirectoryChild(
                        directory=obj,
                        name=name,
                        content_id=row["content_id"],
                        size=row["artifact__size"],
                        last_modified=row["member_created"],
                    )
                )
            MavenDirectoryChild.objects.bulk_create(
                pending,
                update_conflicts=True,
                unique_fields=["directory", "name"],
                update_fields=["content_id", "size", "last_modified"],
            )

        if rebuild:
            # Bootstrap/recovery only. Incremental versions never enumerate the repository.
            MavenDirectory.objects.filter(repository=repository).delete()
            for batch in batches(files(version, pages=False).iterator(chunk_size=1000)):
                replace(batch)
            old_pages = MavenIndexPage.objects.filter(
                pk__in=memberships(version).values("content_id")
            ).values("pk", "path", "sha256")
            for page in old_pages.iterator(chunk_size=1000):
                digest = hashlib.sha256(page["path"].encode("utf-8")).hexdigest()
                obj = MavenDirectory.objects.filter(repository=repository, path_hash=digest).first()
                if obj is None:
                    removed_pages.add(page["pk"])
                else:
                    obj.page_id, obj.page_sha256 = page["pk"], page["sha256"]
                    obj.save(update_fields=["page_id", "page_sha256"])
                    mark_dirty(obj)
        else:
            for paths in batches(changed_paths(version, pages=False).iterator(chunk_size=1000)):
                for path in paths:
                    parent, _, name = path.rpartition("/")
                    obj = directory(parent + "/" if parent else "")
                    obj.children.filter(name=name).delete()
                    mark_dirty(obj)
                replace(files(version, paths, pages=False))

        # Remove empty directories bottom-up. No descendant dates/sizes propagate.
        while True:
            empty = list(
                directories.filter(dirty=True, children__isnull=True)
                .annotate(path_length=Length("path"))
                .order_by("-path_length")[:1000]
            )
            if not empty:
                break
            for obj in sorted(empty, key=lambda item: len(item.path), reverse=True):
                if obj.page_id:
                    removed_pages.add(obj.page_id)
                directory_cache.pop(obj.path_hash, None)
                if obj.path:
                    parent_path, _, name = obj.path.rstrip("/").rpartition("/")
                    parent = directory(parent_path + "/" if parent_path else "")
                    parent.children.filter(name=name + "/").delete()
                    mark_dirty(parent)
                obj.delete()

        # Bound HTML retained in memory by batches; each page is rendered once.
        for group in batches(
            directories.filter(dirty=True).order_by("pk").iterator(chunk_size=64), 64
        ):
            pages = []
            changed = {}
            for obj in group:
                children = list(
                    obj.children.order_by("name").values("name", "size", "last_modified")
                )
                raw = Handler.render_html(
                    [c["name"] for c in children],
                    path=obj.path,
                    sizes={c["name"]: c["size"] for c in children if c["size"] is not None},
                    dates={c["name"]: c["last_modified"] for c in children if c["last_modified"]},
                ).encode("utf-8")
                if hashlib.sha256(raw).hexdigest() == obj.page_sha256:
                    directories.filter(pk=obj.pk).update(dirty=False)
                    continue
                pages.append((obj.path, raw))
                changed[obj.path] = obj
            artifacts = _save_artifacts_batch(pages, repository.pulp_domain)
            added = []
            for path, artifact in artifacts.items():
                obj = changed[path]
                if obj.page_id:
                    removed_pages.add(obj.page_id)
                page, _ = MavenIndexPage.objects.get_or_create(
                    path=path, sha256=artifact.sha256, _pulp_domain=repository.pulp_domain
                )
                ContentArtifact.objects.get_or_create(
                    content=page, relative_path=path + "index.html", defaults={"artifact": artifact}
                )
                obj.page_id, obj.page_sha256 = page.pk, artifact.sha256
                obj.dirty = False
                obj.save(update_fields=["page_id", "page_sha256", "dirty"])
                added.append(page.pk)
            if removed_pages:
                version.remove_content(MavenIndexPage.objects.filter(pk__in=removed_pages))
                removed_pages.clear()
            if added:
                version.add_content(MavenIndexPage.objects.filter(pk__in=added))
        if removed_pages:
            version.remove_content(MavenIndexPage.objects.filter(pk__in=removed_pages))

        # Core may later discard a no-op version without calling on_new_version.
        # A failed finalizer leaves a UUID that cannot match the next predecessor.
        state.version_id = (
            version.pk
            if version.added().exists() or version.removed().exists()
            else previous.pk
            if previous
            else None
        )
        state.save(update_fields=["version_id"])
