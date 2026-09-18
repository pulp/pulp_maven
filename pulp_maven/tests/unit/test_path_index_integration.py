"""Exercise the real repository lifecycle, SQL extraction, and directory updates."""

import json
import os
import subprocess
import sys
import textwrap
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.apps import apps
from django.db import connection
from django.db.models.signals import post_migrate
from django.test.utils import CaptureQueriesContext

from pulpcore.plugin.models import ContentArtifact, Domain, RepositoryContent
from pulpcore.plugin.util import get_default_domain, get_domain, set_domain

from pulp_maven.app.models import (
    MavenArtifact,
    MavenRepository,
)
from pulp_maven.app.path_index.format import InvalidIndex
from pulp_maven.app.tasks import _save_artifact

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module", autouse=True)
def post_flush_apps_registry():
    # Core's post_migrate receivers require the registry supplied by migrate.
    # Django's TransactionTestCase flush omits it; keep real transactional tests
    # and the normal post-flush initialization by supplying the current registry.
    send = post_migrate.send

    def with_apps(sender, **kwargs):
        if "apps" not in kwargs:
            # Core also retains the default Domain object across a flush.
            default = get_default_domain()
            if not Domain.objects.filter(pk=default.pk).exists():
                default.save(force_insert=True, skip_hooks=True)
            set_domain(default)
            kwargs["apps"] = apps
        return send(sender, **kwargs)

    with patch.object(post_migrate, "send", with_apps):
        yield


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
    return MavenRepository.objects.create(name=str(uuid4()), pulp_labels={"path_index": "true"})


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


@pytest.fixture
def storage(repository, tmp_path, monkeypatch, settings):
    from pulp_maven.app.path_index.s3 import S3IndexStore
    from pulp_maven.tests.unit.test_path_index_s3 import FileS3

    remote = FileS3(tmp_path / "s3")
    index = S3IndexStore(
        remote,
        "indices",
        "maven",
        tmp_path / "cache",
        str(repository.pulp_domain_id),
        str(repository.pk),
        max_cache_bytes=16 * 1024**2,
    )
    monkeypatch.setattr("pulp_maven.app.tasks.path_index.store", lambda repo: index)
    monkeypatch.setattr("pulp_maven.app.path_index.publish.store", lambda repo: index)
    monkeypatch.setattr("pulp_maven.app.path_index.cache.store", lambda repo: index)
    return index


def test_publication_precedes_completion_and_retention(repository, storage):
    from pulp_maven.app.path_index.cache import prepare
    from pulp_maven.app.path_index.state import INFO_KEY

    repository.retain_repo_versions = 1
    repository.save()
    a, b = content("com/example/lib/1.0/a.jar"), content("com/example/lib/1.0/b.jar")
    first = change(repository, [a])
    update = storage.update

    def before_complete(version_id, *args):
        assert repository.versions.filter(pk=first.pk, complete=True).exists()
        assert repository.versions.filter(pk=version_id, complete=False).exists()
        assert RepositoryContent.objects.filter(repository=repository, content=a).exists()
        return update(version_id, *args)

    with patch.object(storage, "update", side_effect=before_complete):
        final = change(repository, [b], [a])
    assert not repository.versions.filter(pk=first.pk).exists()
    assert not RepositoryContent.objects.filter(repository=repository, content=a).exists()
    assert INFO_KEY in final.info
    assert len(json.dumps(final.info[INFO_KEY])) < 256
    with prepare(repository, final.pk) as view:
        assert view.lookup("com/example/lib/1.0/a.jar") is None
        assert view.lookup("com/example/lib/1.0/b.jar")
        with CaptureQueriesContext(connection) as sql:
            for _ in range(100):
                assert view.lookup("missing.jar") is None
        assert len(sql) == 0


def test_s3_failure_aborts_modify_and_preserves_orphans(repository, storage):
    from pulpcore.plugin.tasking import add_and_remove

    from pulp_maven.app.path_index.s3 import IndexUnavailable

    repository.retain_repo_versions = 1
    repository.save()
    a, b = content("com/example/lib/1.0/a.jar"), content("com/example/lib/1.0/b.jar")
    first = change(repository, [a])
    with patch.object(storage.client, "put_object", side_effect=OSError("S3 unavailable")):
        with pytest.raises(IndexUnavailable):
            add_and_remove(repository.pk, [b.pk], [a.pk])
    assert repository.latest_version().pk == first.pk
    assert first.content.filter(pk=a.pk).exists()
    assert not first.content.filter(pk=b.pk).exists()
    assert MavenArtifact.objects.filter(pk=b.pk).exists()
    assert ContentArtifact.objects.filter(content=b, artifact__isnull=False).exists()
    assert not repository.versions.filter(complete=False).exists()
    add_and_remove(repository.pk, [b.pk], [a.pk])
    assert repository.latest_version().content.filter(pk=b.pk).exists()


def test_db_failure_does_not_advertise_successful_s3_upload(repository, storage):
    from pulp_maven.app.models import MavenDistribution
    from pulp_maven.app.path_index.content import descriptor

    first = change(repository, [content("com/example/lib/1.0/a.jar")])
    before = len(storage.client.events("PUT"))
    distro = MavenDistribution.objects.create(
        name=str(uuid4()), base_path=str(uuid4()), repository=repository
    )
    with patch(
        "pulpcore.plugin.models.RepositoryVersion._compute_counts", side_effect=RuntimeError("DB")
    ):
        with pytest.raises(RuntimeError):
            change(repository, [content("com/example/lib/1.0/b.jar")])
    assert len(storage.client.events("PUT")) > before
    assert repository.latest_version().pk == first.pk
    assert descriptor(distro)[1] == str(first.pk)


def test_noop_does_not_publish(repository, storage):
    a = content("com/example/lib/1.0/a.jar")
    first = change(repository, [a])
    before = len(storage.client.events("PUT"))
    noop = change(repository, [a])
    assert not repository.versions.filter(pk=noop.pk).exists()
    assert repository.latest_version().pk == first.pk
    assert len(storage.client.events("PUT")) == before


def test_removing_label_disables_writes_and_serving(repository, storage, settings):
    from pulp_maven.app.models import MavenDistribution
    from pulp_maven.app.path_index.content import descriptor
    from pulp_maven.app.path_index.state import INFO_KEY

    settings.MAVEN_PATH_INDEX_REFRESH_SECONDS = 0
    change(repository, [content("com/example/lib/1.0/a.jar")])
    distro = MavenDistribution.objects.create(
        name=str(uuid4()), base_path=str(uuid4()), repository=repository
    )
    assert descriptor(distro)
    before = len(storage.client.events("PUT"))
    repository.pulp_labels = {}
    repository.save()
    assert descriptor(distro) is None
    version = change(repository, [content("com/example/lib/1.0/b.jar")])
    assert INFO_KEY not in version.info
    assert len(storage.client.events("PUT")) == before


def test_bulk_modify_publishes_one_delta_and_preserves_pinned_version(repository, storage):
    from pulpcore.plugin.tasking import add_and_remove

    from pulp_maven.app.path_index.cache import prepare

    first = change(repository, [content("com/example/lib/1.0/a.jar")])
    uploads = [content(f"com/example/lib/1.0/file{i}.jar") for i in range(200)]
    before = len(storage.client.events("PUT"))
    add_and_remove(repository.pk, [u.pk for u in uploads], [])
    latest = repository.latest_version()
    assert len(storage.client.events("PUT")) == before + 2
    with prepare(repository, latest.pk) as view:
        for i in range(200):
            assert view.lookup(f"com/example/lib/1.0/file{i}.jar")
    with prepare(repository, first.pk) as view:
        assert view.lookup("com/example/lib/1.0/file0.jar") is None


def test_bootstrap_retained_version_without_new_version(repository, storage, settings):
    from pulp_maven.app.path_index.cache import prepare
    from pulp_maven.app.path_index.state import INFO_KEY
    from pulp_maven.app.tasks.path_index import build_path_index

    repository.pulp_labels = {}
    repository.save()
    version = change(repository, [content("com/example/lib/1.0/a.jar")])
    assert INFO_KEY not in version.info
    repository.pulp_labels = {"path_index": "true"}
    repository.save()
    count = repository.versions.count()
    build_path_index(repository.pk, version.pk)
    assert repository.versions.count() == count
    before = len(storage.client.events("PUT"))
    build_path_index(repository.pk, version.pk)
    assert len(storage.client.events("PUT")) == before
    with prepare(repository, version.pk) as view:
        assert view.lookup("com/example/lib/1.0/a.jar")


def test_compaction_changes_descriptor_for_same_version(repository, storage, settings):
    from pulp_maven.app.models import MavenDistribution
    from pulp_maven.app.path_index.cache import prepare
    from pulp_maven.app.path_index.content import descriptor
    from pulp_maven.app.path_index.s3 import IndexUnavailable
    from pulp_maven.app.tasks.path_index import compact_path_index

    settings.MAVEN_PATH_INDEX_REFRESH_SECONDS = 0
    change(repository, [content("com/example/lib/1.0/a.jar")])
    latest = change(repository, [content("com/example/lib/1.0/b.jar")])
    distro = MavenDistribution.objects.create(
        name=str(uuid4()), base_path=str(uuid4()), repository_version=latest
    )
    before = descriptor(distro)
    compact_path_index(repository.pk)
    after = descriptor(distro)
    assert before[:3] == after[:3] and before[3] != after[3]
    with pytest.raises(IndexUnavailable):
        prepare(repository, latest.pk, digest=before[3])
    with prepare(repository, latest.pk, digest=after[3]) as view:
        assert len(view.segments) == 1
        assert view.lookup("com/example/lib/1.0/b.jar")
    change(repository, [content("com/example/lib/1.0/c.jar")])
    with prepare(repository, repository.latest_version().pk) as view:
        assert view.lookup("com/example/lib/1.0/c.jar")


def test_missing_or_invalid_index_uses_existing_request_flow(repository, storage, settings):
    from pulp_maven.app.models import MavenDistribution
    from pulp_maven.app.path_index.content import descriptor
    from pulp_maven.app.path_index.state import INFO_KEY

    settings.MAVEN_PATH_INDEX_REFRESH_SECONDS = 0
    version = change(repository, [content("com/example/lib/1.0/a.jar")])
    distro = MavenDistribution.objects.create(
        name=str(uuid4()), base_path=str(uuid4()), repository=repository
    )
    assert descriptor(distro)
    version.info[INFO_KEY]["digest"] = "invalid"
    version.save(update_fields=["info"])
    assert descriptor(distro) is None
    version.info = {}
    version.save(update_fields=["info"])
    assert descriptor(distro) is None


def test_descriptor_poll_never_selects_content_ids(repository, storage):
    from pulp_maven.app.models import MavenDistribution
    from pulp_maven.app.path_index.content import descriptor
    from pulp_maven.app.viewsets import MavenRepositoryViewSet

    change(repository, [content("com/example/lib/1.0/a.jar")])
    distro = MavenDistribution.objects.create(
        name=str(uuid4()), base_path=str(uuid4()), repository=repository
    )
    view = MavenRepositoryViewSet()
    view.get_object = lambda: repository
    with CaptureQueriesContext(connection) as sql:
        assert descriptor(distro)
        assert view.path_index_status(None, repository.pk).data["ready"]
    assert not any('"content_ids"' in q["sql"] or '"core_contentartifact"' in q["sql"] for q in sql)
    with CaptureQueriesContext(connection) as sql:
        assert descriptor(distro)
    assert len(sql) == 0


def test_missing_directory_page_aborts_authoritative_index(repository, storage, monkeypatch):
    monkeypatch.setattr(MavenRepository, "_generate_index_pages", lambda *args: None)
    with pytest.raises(InvalidIndex, match="coverage"):
        change(repository, [content("com/example/lib/1.0/a.jar")])
    assert not storage.client.events("PUT")


def test_disk_budget_failure_never_becomes_a_path_miss(repository, storage):
    from pulp_maven.app.path_index.cache import prepare
    from pulp_maven.app.path_index.s3 import CacheFull

    version = change(repository, [content("com/example/lib/1.0/a.jar")])
    storage.max_cache_bytes = 16
    with pytest.raises(CacheFull):
        prepare(repository, version.pk)


def test_manifest_digest_mismatch_rejected(repository, storage):
    from pulp_maven.app.path_index.cache import prepare
    from pulp_maven.app.path_index.state import INFO_KEY

    version = change(repository, [content("com/example/lib/1.0/a.jar")])
    version.info[INFO_KEY]["digest"] = "00" * 32
    version.save(update_fields=["info"])
    with pytest.raises(InvalidIndex):
        prepare(repository, version.pk)


def test_repeated_versions_compact_deltas_and_rebase_at_cap(repository, storage):
    from pulp_maven.app.path_index.cache import prepare

    repository.retain_repo_versions = 1
    repository.save()
    # A small cap reaches the full-rebase boundary with a realistic bounded test.
    storage.store_options["max_segments"] = 3
    for number in range(16):
        latest = change(repository, [content(f"com/example/lib/1.0/file{number}.jar")])
    with prepare(repository, latest.pk) as view:
        assert len(view.segments) <= 3
        for number in range(16):
            assert view.lookup(f"com/example/lib/1.0/file{number}.jar")


@pytest.mark.django_db(transaction=True)
def test_four_processes_share_segment_downloads(repository, storage):
    from pulp_maven.app.tasks.path_index import build_path_index

    change(repository, [content("com/example/lib/1.0/a.jar")])
    build_path_index(repository.pk)
    latest = change(repository, [content("com/example/lib/1.0/b.jar")])
    script = textwrap.dedent("""
        import json, sys
        import django
        django.setup()
        from django.db import connections
        from django.conf import settings
        args = json.loads(sys.argv[1])
        connections.close_all()
        settings.DATABASES['default']['NAME'] = args['database']
        connections['default'].settings_dict['NAME'] = args['database']
        from pulpcore.plugin.util import set_domain
        from pulp_maven.app.models import MavenRepository
        from pulp_maven.app.path_index.s3 import S3IndexStore
        from pulp_maven.app.path_index import cache
        from pulp_maven.tests.unit.test_path_index_s3 import FileS3
        repository = MavenRepository.objects.select_related('pulp_domain').get(pk=args['repo'])
        set_domain(repository.pulp_domain)
        cache.store = lambda repo: S3IndexStore(
            FileS3(args['remote']), 'indices', 'maven', args['cache'],
            str(repository.pulp_domain_id), str(repository.pk), max_cache_bytes=16*1024**2,
        )
        with cache.prepare(repository, args['version']) as view:
            assert view.lookup('com/example/lib/1.0/a.jar')
            assert view.lookup('com/example/lib/1.0/b.jar')
            assert view.lookup('missing.jar') is None
        connections.close_all()
    """)
    args = json.dumps(
        {
            "database": connection.settings_dict["NAME"],
            "repo": str(repository.pk),
            "version": str(latest.pk),
            "remote": str(storage.client.root),
            "cache": str(storage.cache_directory),
        }
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy(),
        )
        for _ in range(4)
    ]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=60)
            assert process.returncode == 0, stdout + stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    gets = [key for key in storage.client.events("GET") if "/segments/" in key]
    assert len(gets) == 2


def test_fresh_namespace_rebuilds_incompatible_profile(repository, storage, settings):
    from pulp_maven.app.path_index.config import profile
    from pulp_maven.app.path_index.state import INFO_KEY
    from pulp_maven.app.tasks.path_index import build_path_index

    version = change(repository, [content("com/example/lib/1.0/a.jar")])
    old = version.info[INFO_KEY]["profile"]
    repository.pulp_domain.storage_settings = {"location": "new-experiment"}
    repository.pulp_domain.save(update_fields=["storage_settings"], skip_hooks=True)
    storage.prefix = "new-experiment"
    build_path_index(repository.pk)
    version.refresh_from_db()
    assert version.info[INFO_KEY]["profile"] != old
    assert version.info[INFO_KEY]["profile"] == profile(repository.pulp_domain)
    assert any(key.startswith("new-experiment/") for key in storage.client.events("PUT"))


@pytest.mark.django_db(transaction=True)
def test_cache_pressure_releases_idle_mappings(repository, storage):
    from unittest.mock import Mock

    from pulp_maven.app.path_index.cache import ViewCache
    from pulp_maven.app.path_index.config import profile
    from pulp_maven.app.path_index.s3 import CacheFull
    from pulp_maven.app.path_index.state import INFO_KEY

    version = change(repository, [content("com/example/lib/1.0/a.jar")])
    key = (
        str(repository.pk),
        str(version.pk),
        profile(repository.pulp_domain),
        version.info[INFO_KEY]["digest"],
    )
    idle, busy, fresh = Mock(), Mock(), Mock()
    cached = ViewCache()
    cached.views["idle"] = [idle, 0]
    cached.views["busy"] = [busy, 1]
    try:
        with patch(
            "pulp_maven.app.path_index.cache.prepare", side_effect=[CacheFull("full"), fresh]
        ):
            cached._load(key)
        idle.close.assert_called_once()
        busy.close.assert_not_called()
        assert cached.views[key][0] is fresh
    finally:
        cached.close()
