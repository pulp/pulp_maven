import asyncio
import contextlib
import logging
import random
import time

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections

from pulpcore.plugin.content import app
from pulpcore.plugin.models import RepositoryVersion

from pulp_maven.app.models import MavenRepository
from pulp_maven.app.path_index.cache import cache
from pulp_maven.app.path_index.config import enabled, profile
from pulp_maven.app.path_index.metrics import record_prewarm_scan
from pulp_maven.app.path_index.state import descriptor

log = logging.getLogger(__name__)


def _prewarm_keys():
    close_old_connections()
    keys = []
    try:
        repositories = MavenRepository.objects.select_related("pulp_domain").filter(
            pulp_labels__path_index="true"
        )
        for repository in repositories.iterator():
            if not enabled(repository):
                continue
            version = (
                RepositoryVersion.objects.filter(repository=repository, complete=True)
                .only("pk", "info")
                .order_by("-number")
                .first()
            )
            if version is None:
                continue
            value = descriptor(version, repository)
            if value is not None:
                keys.append(
                    (
                        str(repository.pk),
                        str(version.pk),
                        profile(repository.pulp_domain),
                        value["digest"],
                    )
                )
    finally:
        close_old_connections()
    return keys


async def _prewarm_once():
    started = time.monotonic()
    keys = await sync_to_async(_prewarm_keys, thread_sensitive=True)()
    scheduled = cache().prewarm(keys)
    duration = time.monotonic() - started
    record_prewarm_scan(duration, scheduled)
    log.info(
        "Maven path index prewarm scheduled",
        extra={"repository_versions": len(keys), "scheduled": scheduled},
    )


async def _periodic_prewarm():
    # Refresh before the first request misses; request-time loading remains only
    # the fallback for a publish that lands between scans.
    interval = settings.MAVEN_PATH_INDEX_PREWARM_INTERVAL_SECONDS
    jitter = settings.MAVEN_PATH_INDEX_PREWARM_JITTER_SECONDS
    await asyncio.sleep(random.uniform(0, interval))
    while True:
        await _prewarm_once()
        await asyncio.sleep(max(0, interval + random.uniform(-jitter, jitter)))


async def _prewarm_path_indexes(app):
    await _prewarm_once()
    task = asyncio.create_task(_periodic_prewarm())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        cache().close()


app.cleanup_ctx.append(_prewarm_path_indexes)
