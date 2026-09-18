"""Request semantics and shared HTML cache without a database or an S3 server."""

import asyncio
import fcntl
import hashlib
import io
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from pulpcore.plugin.models import Domain

from pulp_maven.app.path_index.content import HTMLResponse, artifact, indexed_response
from pulp_maven.app.path_index.format import Entry
from pulp_maven.app.path_index.html_cache import open_html
from pulp_maven.app.path_index.http import IndexedArtifactResponse


def entry(raw=b"<html>listing</html>"):
    return Entry.for_path("index.html", hashlib.sha256(raw).hexdigest(), len(raw), 1_700_000_000)


def domain():
    return Domain(pk=uuid4(), name="test", storage_class="s3", storage_settings={})


def test_storage_namespace_changes_invalidate_cached_profile(settings):
    from pulp_maven.app.path_index.config import profile

    scope = domain()
    previous = profile(scope)
    settings.MAVEN_PATH_INDEX_S3_PREFIX = "fresh-experiment"
    assert profile(scope) != previous


def test_conditional_html_does_not_open_storage():
    async def exercise():
        item = entry()
        app = web.Application()

        async def handle(request):
            return HTMLResponse(item, domain(), {})

        app.router.add_get("/", handle)
        async with TestClient(TestServer(app)) as client:
            with patch(
                "pulp_maven.app.path_index.content.open_html", side_effect=AssertionError("opened")
            ):
                result = await client.get(
                    "/", headers={"If-None-Match": f'"other", W/"{item.artifact_sha256.hex()}"'}
                )
                assert result.status == 304
                assert await result.read() == b""
                result = await client.get("/", headers={"If-Match": '"other"'})
                assert result.status == 412
                result = await client.head("/")
                assert result.status == 200
                assert result.headers["Content-Length"] == str(item.size)

    asyncio.run(exercise())


def test_if_none_match_precedes_if_modified_since():
    async def exercise():
        raw = b"<html>listing</html>"
        app = web.Application()

        async def handle(request):
            return HTMLResponse(entry(raw), domain(), {})

        app.router.add_get("/", handle)
        stream = io.BytesIO(raw)
        with (
            patch("pulp_maven.app.path_index.content.artifact", return_value=Mock()),
            patch("pulp_maven.app.path_index.content.open_html", return_value=(stream, stream)),
        ):
            async with TestClient(TestServer(app)) as client:
                result = await client.get(
                    "/",
                    headers={
                        "If-None-Match": '"different"',
                        "If-Modified-Since": "Wed, 01 Jan 2031 00:00:00 GMT",
                    },
                )
                assert result.status == 200
                assert await result.read() == raw
        assert stream.closed

    asyncio.run(exercise())


def test_shared_html_cache_and_budget_pin(tmp_path, settings):
    settings.MAVEN_PATH_INDEX_CACHE_DIR = str(tmp_path)
    raw = b"<html>listing</html>"
    settings.MAVEN_PATH_INDEX_HTML_BYTES = len(raw)
    scope = domain()
    source = Mock()
    source.file.open.side_effect = lambda mode: io.BytesIO(raw)
    stream, owner = open_html(entry(raw), scope, source)
    assert stream.read() == raw
    # A second worker gets the same bytes without opening the object again.
    other, other_owner = open_html(entry(raw), scope, source)
    assert other.read() == raw
    assert source.file.open.call_count == 1
    # Pinned content cannot be evicted to make space for another page.
    raw2 = b"<html>changed</html>"
    source2 = Mock()
    source2.file.open.side_effect = lambda mode: io.BytesIO(raw2)
    third, third_owner = open_html(entry(raw2), scope, source2)
    assert third.read() == raw2
    assert sum(p.stat().st_size for p in tmp_path.glob("html/*/*.html")) <= len(raw)
    owner.close()
    other_owner.close()
    third_owner.close()


def test_html_cache_read_failure_releases_pin(tmp_path, settings):
    settings.MAVEN_PATH_INDEX_CACHE_DIR = str(tmp_path)
    raw = b"<html>listing</html>"
    scope = domain()
    source = Mock()
    source.file.open.side_effect = lambda mode: io.BytesIO(raw)
    _, owner = open_html(entry(raw), scope, source)
    owner.close()
    page = next(tmp_path.glob("html/*/*.html"))
    original_open = Path.open

    def fail(self, *args, **kwargs):
        if self == page:
            raise OSError("read failure")
        return original_open(self, *args, **kwargs)

    with patch.object(Path, "open", fail):
        stream, owner = open_html(entry(raw), scope, source)
        assert stream.read() == raw
        owner.close()
    with page.with_suffix(".lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_lookup_deletion_and_redirect_variants(settings):
    settings.MAVEN_PATH_INDEX_MODE = "serve"
    distro = SimpleNamespace(remote_id=None, checkpoint=False, content_guard=None)
    view = Mock()
    view.lookup.side_effect = lambda path: entry() if path == "1.0/" else None

    @contextmanager
    def lease(key):
        yield view

    with (
        patch(
            "pulp_maven.app.path_index.content.descriptor",
            return_value=("repo", "version", "profile"),
        ),
        patch("pulp_maven.app.path_index.content.cache", return_value=SimpleNamespace(lease=lease)),
    ):
        with pytest.raises(web.HTTPNotFound):
            indexed_response(distro, "removed.jar")
        with pytest.raises(web.HTTPMovedPermanently) as result:
            indexed_response(distro, "1.0")
        assert result.value.location == "1.0/"


def test_artifact_size_does_not_query_storage():
    scope = domain()
    # Only inspect the size contract; storage_path needs Pulp's active domain.
    with patch("pulpcore.plugin.models.Artifact.storage_path", return_value="artifact/test"):
        obj = artifact(entry(), scope)
        obj.file.storage = Mock()
        assert obj.file.size == entry().size
        obj.file.storage.size.assert_not_called()


def test_artifact_redirect_preserves_head_and_indexed_validators(settings):
    settings.MAVEN_PATH_INDEX_REDIRECT_THRESHOLD = 10
    scope = domain()
    scope.storage_class = "storages.backends.s3.S3Storage"
    scope.redirect_to_object_storage = False
    storage = Mock()
    storage.url.return_value = "https://objects.example.test/artifact"
    scope.get_storage = lambda: storage
    obj = SimpleNamespace(file=SimpleNamespace(name="artifact/hash"))

    async def exercise():
        async def handle(request):
            return IndexedArtifactResponse(obj, entry(), scope, "lib.jar", {})

        app = web.Application()
        app.router.add_get("/", handle)
        async with TestClient(TestServer(app)) as client:
            response = await client.head("/", allow_redirects=False)
            assert response.status == 302
            assert response.headers["Last-Modified"]
            assert response.headers["ETag"] == f'"{entry().artifact_sha256.hex()}"'
            assert storage.url.call_args.kwargs["http_method"] == "HEAD"
            storage.reset_mock()
            response = await client.get("/", headers={"If-None-Match": "*"}, allow_redirects=False)
            assert response.status == 304
            storage.url.assert_not_called()

    asyncio.run(exercise())


def test_indexed_artifact_streaming_ranges_and_head():
    raw = b"0123456789"
    scope = domain()
    scope.storage_class = "pulpcore.app.models.storage.FileSystem"

    class SizedBytes(io.BytesIO):
        size = len(raw)

    async def exercise():
        opened = []

        async def handle(request):
            file = SizedBytes(raw)
            opened.append(file)
            return IndexedArtifactResponse(
                SimpleNamespace(file=file), entry(raw), scope, "lib.jar", {}
            )

        app = web.Application()
        app.router.add_get("/", handle)
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/", headers={"Range": "bytes=2-4"})
            assert response.status == 206
            assert await response.read() == b"234"
            assert response.headers["Content-Range"] == "bytes 2-4/10"
            response = await client.get("/", headers={"Range": "bytes=30-40"})
            assert response.status == 416
            response = await client.head("/")
            assert response.status == 200 and response.headers["Content-Length"] == "10"
        for file in opened:
            file.close()

    asyncio.run(exercise())
