"""Bounded background preparation and process-local leases on shared mmap files."""

import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from django.conf import settings
from django.db import close_old_connections

from pulpcore.plugin.models import RepositoryVersion
from pulpcore.plugin.util import set_domain

from .config import enabled, profile, store
from .s3 import CacheFull, IndexUnavailable
from .state import descriptor, read

log = logging.getLogger(__name__)
LOAD_WORKERS = 2


def prepare(repository, version_id, *, digest=None):
    version = RepositoryVersion.objects.only("pk", "info").get(
        pk=version_id, repository=repository, complete=True
    )
    value = descriptor(version, repository)
    if value is None or (digest is not None and value["digest"] != digest):
        raise IndexUnavailable("The selected manifest changed or has not been built")
    index = store(repository)
    return index.open(read(index, version, repository))


class ViewCache:
    """Leases prevent eviction during lookup; slow work never runs in a request."""

    def __init__(self):
        self.lock = threading.RLock()
        self.views = OrderedDict()
        self.pending = {}
        self.retry = {}
        self.pool = ThreadPoolExecutor(max_workers=LOAD_WORKERS, thread_name_prefix="maven-index")

    def _load(self, key):
        from pulp_maven.app.models import MavenRepository

        close_old_connections()
        view = None
        try:
            repository = MavenRepository.objects.select_related("pulp_domain").get(pk=key[0])
            set_domain(repository.pulp_domain)
            if not enabled(repository) or profile(repository.pulp_domain) != key[2]:
                raise IndexUnavailable("Index configuration changed")
            if not RepositoryVersion.objects.filter(
                pk=key[1], repository=repository, complete=True
            ).exists():
                raise IndexUnavailable("The selected version no longer exists")
            try:
                view = prepare(repository, key[1], digest=key[3])
            except CacheFull:
                # Cached views hold file pins even without active lookup leases.
                # Release this worker's unused mappings before retrying a fill;
                # otherwise old versions could keep every future version cold.
                with self.lock:
                    for old_key in list(self.views):
                        if self.views[old_key][1] == 0:
                            self.views.pop(old_key)[0].close()
                view = prepare(repository, key[1], digest=key[3])
            with self.lock:
                while len(self.views) >= settings.MAVEN_PATH_INDEX_MAX_VIEWS:
                    victim = next((k for k, v in self.views.items() if v[1] == 0), None)
                    if victim is None:
                        raise CacheFull("All mapped version views are in use")
                    self.views.pop(victim)[0].close()
                self.views[key] = [view, 0]
                view = None
        except Exception:
            log.debug("Path index view unavailable", exc_info=True)
        finally:
            if view is not None:
                view.close()
            close_old_connections()
            with self.lock:
                self.pending.pop(key, None)
                self.retry[key] = time.monotonic() + settings.MAVEN_PATH_INDEX_REFRESH_SECONDS
                if len(self.retry) > 1024:
                    self.retry.pop(next(iter(self.retry)))

    @contextmanager
    def lease(self, key):
        with self.lock:
            item = self.views.get(key)
            if item is not None:
                item[1] += 1
                self.views.move_to_end(key)
            elif (
                key not in self.pending
                and self.retry.get(key, 0) <= time.monotonic()
                and len(self.pending) < LOAD_WORKERS
            ):
                self.pending[key] = True
                self.pool.submit(self._load, key)
        try:
            yield item[0] if item else None
        finally:
            if item:
                with self.lock:
                    item[1] -= 1

    def close(self):
        self.pool.shutdown(wait=True)
        with self.lock:
            for view, _ in self.views.values():
                view.close()
            self.views.clear()


_cache = None
_pid = None
_cache_lock = threading.Lock()


def cache():
    global _cache, _pid
    with _cache_lock:
        if _cache is None or _pid != os.getpid():
            _cache, _pid = ViewCache(), os.getpid()
        return _cache
