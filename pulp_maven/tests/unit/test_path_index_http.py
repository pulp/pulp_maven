"""Indexed artifact and inline listing responses without a database or an S3 server."""

import asyncio
import hashlib
import io
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from pulpcore.plugin.models import Domain

from pulp_maven.app.path_index.content import artifact, indexed_response
from pulp_maven.app.path_index.format import Entry
from pulp_maven.app.path_index.http import IndexedArtifactResponse


def entry(raw=b"<html>listing</html>"):
    return Entry.for_path("index.html", hashlib.sha256(raw).hexdigest(), len(raw), 1_700_000_000)


def domain():
    return Domain(pk=uuid4(), name="test", storage_class="s3", storage_settings={})


def test_storage_namespace_changes_invalidate_cached_profile(settings):
    from pulp_maven.app.path_index.config import profile

    scope = domain()
    previous = profile(scope)
    scope.storage_settings = {"location": "fresh-experiment"}
    assert profile(scope) != previous


@pytest.mark.parametrize("path", ["", "com/example/", "index.html", "com/example/index.html"])
@pytest.mark.parametrize(
    "backend", ["storages.backends.s3.S3Storage", "storages.backends.azure_storage.AzureStorage"]
)
def test_html_paths_stream_inline_with_cache_headers(path, backend, settings):
    settings.MAVEN_PATH_INDEX_REDIRECT_THRESHOLD = 1
    raw = b"<html>listing</html>"
    scope = domain()
    scope.storage_class = backend
    scope.redirect_to_object_storage = True
    scope.get_storage = Mock()
    distro = SimpleNamespace(remote_id=None, checkpoint=False, content_guard=None)
    view = Mock()
    view.lookup.return_value = entry(raw)

    class SizedBytes(io.BytesIO):
        size = len(raw)

    @contextmanager
    def lease(key):
        yield view

    async def exercise():
        async def handle(request):
            response = indexed_response(distro, path)
            assert isinstance(response, IndexedArtifactResponse)
            return response

        app = web.Application()
        app.router.add_get("/", handle)
        stream = SizedBytes(raw)
        with (
            patch(
                "pulp_maven.app.path_index.content.descriptor",
                return_value=("repo", "version", "profile", "digest"),
            ),
            patch(
                "pulp_maven.app.path_index.content.cache", return_value=SimpleNamespace(lease=lease)
            ),
            patch("pulp_maven.app.path_index.content.get_domain", return_value=scope),
            patch(
                "pulp_maven.app.path_index.content.artifact",
                return_value=SimpleNamespace(file=stream),
            ),
            patch(
                "pulp_maven.app.path_index.content.Handler.response_headers",
                return_value={
                    "Content-Type": "application/octet-stream",
                    "Content-Disposition": "attachment",
                },
            ),
        ):
            async with TestClient(TestServer(app)) as client:
                response = await client.get("/", allow_redirects=False)
                assert response.status == 200
                assert await response.read() == raw
                assert response.headers["Content-Type"] == "text/html; charset=utf-8"
                assert response.headers["Content-Disposition"] == "inline"
                assert response.headers["Cache-Control"] == "public, max-age=0, must-revalidate"
                assert response.headers["ETag"] == f'"{entry(raw).artifact_sha256.hex()}"'
                assert response.headers["Last-Modified"]
                assert response.headers["Content-Length"] == str(len(raw))
                assert "Location" not in response.headers
        scope.get_storage.assert_not_called()
        view.lookup.assert_called_once_with(path)
        return stream

    # ArtifactResponse closes in the executor; asyncio.run drains it on exit.
    assert asyncio.run(exercise()).closed


def test_conditional_html_and_head_do_not_read_storage(settings):
    settings.MAVEN_PATH_INDEX_REDIRECT_THRESHOLD = 1
    scope = domain()
    scope.storage_class = "storages.backends.s3.S3Storage"
    scope.redirect_to_object_storage = True
    scope.get_storage = Mock()
    item = entry()
    file = Mock(size=item.size)
    file.read.side_effect = AssertionError("read storage")
    file.seek.side_effect = AssertionError("opened storage")

    async def exercise():
        app = web.Application()

        async def handle(request):
            return IndexedArtifactResponse(
                SimpleNamespace(file=file), item, scope, "index.html", {}, inline_html=True
            )

        app.router.add_get("/", handle)
        async with TestClient(TestServer(app)) as client:
            result = await client.get(
                "/", headers={"If-None-Match": f'"other", W/"{item.artifact_sha256.hex()}"'}
            )
            assert result.status == 304
            assert await result.read() == b""
            assert result.headers["Cache-Control"] == "public, max-age=0, must-revalidate"
            assert result.headers["ETag"] == f'"{item.artifact_sha256.hex()}"'
            assert result.headers["Last-Modified"]
            result = await client.get("/", headers={"If-Match": '"other"'})
            assert result.status == 412
            result = await client.get(
                "/", headers={"If-Modified-Since": "Wed, 01 Jan 2031 00:00:00 GMT"}
            )
            assert result.status == 304
            result = await client.head("/", allow_redirects=False)
            assert result.status == 200
            assert await result.read() == b""
            assert result.headers["Content-Length"] == str(item.size)
            assert result.headers["Content-Type"] == "text/html; charset=utf-8"
            assert result.headers["Content-Disposition"] == "inline"
        file.read.assert_not_called()
        file.seek.assert_not_called()
        scope.get_storage.assert_not_called()

    asyncio.run(exercise())


def test_if_none_match_precedes_if_modified_since():
    raw = b"<html>listing</html>"

    class SizedBytes(io.BytesIO):
        size = len(raw)

    async def exercise():
        app = web.Application()
        stream = SizedBytes(raw)

        async def handle(request):
            return IndexedArtifactResponse(
                SimpleNamespace(file=stream),
                entry(raw),
                domain(),
                "index.html",
                {},
                inline_html=True,
            )

        app.router.add_get("/", handle)
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
        return stream

    assert asyncio.run(exercise()).closed


def test_lookup_deletion_and_redirect_variants(settings):
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
