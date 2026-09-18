"""Optional content hook. Core still authorizes every request before this hook."""

import threading
import time
from collections import OrderedDict

from aiohttp import web
from django.conf import settings
from django.db.models.fields.files import FieldFile

from pulpcore.plugin.content import Handler
from pulpcore.plugin.models import Artifact, RepositoryVersion
from pulpcore.plugin.util import get_domain

from .cache import cache
from .config import enabled, profile
from .format import InvalidIndex
from .http import SUPPORTED_BACKENDS, IndexedArtifactResponse
from .state import descriptor as version_descriptor

_descriptors = OrderedDict()
_lock = threading.Lock()


def descriptor(distribution):
    """Bound DB resolution by TTL; never cache a Django membership/content queryset."""
    from pulp_maven.app.models import MavenRepository

    key = (distribution.pk, distribution.repository_id, distribution.repository_version_id)
    with _lock:
        value = _descriptors.get(key)
        if value and value[0] > time.monotonic():
            return value[1]
    version = None
    if distribution.repository_version_id:
        version = (
            RepositoryVersion.objects.filter(pk=distribution.repository_version_id, complete=True)
            .only("pk", "repository_id", "info")
            .first()
        )
    repository_id = version.repository_id if version else distribution.repository_id
    repository = MavenRepository.objects.filter(pk=repository_id).first()
    result = None
    if repository and enabled(repository) and repository.pulp_domain_id == get_domain().pk:
        if version is None:
            version = (
                RepositoryVersion.objects.filter(repository=repository, complete=True)
                .only("pk", "info")
                .order_by("-number")
                .first()
            )
        if version:
            try:
                value = version_descriptor(version, repository)
            except InvalidIndex:
                value = None
            if value:
                result = (
                    str(repository.pk),
                    str(version.pk),
                    profile(get_domain()),
                    value["digest"],
                )
    with _lock:
        _descriptors[key] = (time.monotonic() + settings.MAVEN_PATH_INDEX_REFRESH_SECONDS, result)
        if len(_descriptors) > 1024:
            _descriptors.popitem(last=False)
    return result


class IndexedFile(FieldFile):
    """Core delivery can use known size without an object-storage HEAD."""

    @property
    def size(self):
        return self.instance.size


def artifact(entry, domain):
    obj = Artifact(sha256=entry.artifact_sha256.hex(), size=entry.size, pulp_domain=domain)
    obj.file = IndexedFile(obj, Artifact._meta.get_field("file"), obj.storage_path(None))
    return obj


def indexed_response(distribution, path):
    if settings.MAVEN_PATH_INDEX_MODE == "off":
        return None
    if distribution.remote_id or distribution.checkpoint:
        return None
    # Redirect guards implement a separate URL/signature contract.
    guard = distribution.content_guard
    if guard and guard.pulp_type == "core.content_redirect":
        return None
    key = descriptor(distribution)
    if key is None:
        return None
    with cache().lease(key) as view:
        if view is None or settings.MAVEN_PATH_INDEX_MODE != "serve":
            return None
        entry = view.lookup(path)
        if entry is None and path and not path.endswith("/") and view.lookup(path + "/"):
            raise web.HTTPMovedPermanently(path.rsplit("/", 1)[-1] + "/")
        if entry is None:
            raise web.HTTPNotFound()
        inline_html = (
            not path or path.endswith("/") or path == "index.html" or path.endswith("/index.html")
        )
        domain = get_domain()
        if not inline_html and domain.storage_class not in SUPPORTED_BACKENDS:
            return None
        return IndexedArtifactResponse(
            artifact(entry, domain),
            entry,
            domain,
            path,
            Handler.response_headers(path, distribution),
            inline_html=inline_html,
        )
