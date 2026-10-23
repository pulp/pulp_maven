from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from pulp_maven.app.metadata_cache import cache_downloader, is_metadata_path


def test_metadata_paths():
    for suffix in ("", ".md5", ".sha1", ".sha224", ".sha256", ".sha384", ".sha512"):
        assert is_metadata_path(f"org/example/maven-metadata.xml{suffix}")
    assert not is_metadata_path("org/example/example.pom.sha256")
    assert not is_metadata_path(".meta/prefixes.txt")


class TestMetadataCache(IsolatedAsyncioTestCase):
    def setUp(self):
        self.remote = SimpleNamespace(
            pk="remote", pulp_domain_id="domain", pulp_last_updated="today", metadata_cache_ttl=300
        )
        self.values = {}
        self.cache = AsyncMock()
        self.cache.get.side_effect = lambda key, base_key: self.values.get(base_key)
        self.cache.set.side_effect = lambda key, value, expires, base_key: self.values.update(
            {base_key: value}
        )
        patcher = patch("pulp_maven.app.metadata_cache.get_cache", return_value=self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)

    def downloader(self):
        obj = SimpleNamespace(
            url="https://example.org/maven-metadata.xml",
            headers_ready_callback=AsyncMock(),
            handle_data=AsyncMock(),
            finalize=AsyncMock(),
        )

        async def fetch(extra_data=None):
            await obj.headers_ready_callback({"Content-Type": "application/xml"})
            await obj.handle_data(b"<metadata/>")
            await obj.finalize()
            return "result"

        obj.run = AsyncMock(side_effect=fetch)
        return obj

    async def download(self, expected_fetch=True):
        obj = self.downloader()
        fetch = obj.run
        await cache_downloader(obj, self.remote).run()
        self.assertEqual(fetch.await_count, int(expected_fetch))
        obj.handle_data.assert_awaited_once_with(b"<metadata/>")
        obj.finalize.assert_awaited_once()

    async def test_hit_and_expiration(self):
        await self.download()
        self.assertEqual(self.cache.set.call_args.kwargs["expires"], 300)
        await self.download(expected_fetch=False)
        self.assertEqual(self.cache.set.await_count, 1)  # Hits do not renew the TTL.
        self.values.clear()  # Simulate Redis expiration.
        await self.download()

    async def test_remote_isolation(self):
        for field in ("pk", "pulp_domain_id", "pulp_last_updated", "metadata_cache_ttl"):
            with self.subTest(field=field):
                self.values.clear()
                await self.download()
                original = getattr(self.remote, field)
                setattr(self.remote, field, 600 if field == "metadata_cache_ttl" else "changed")
                await self.download()
                setattr(self.remote, field, original)

    async def test_unavailable_cache(self):
        # Pulpcore's cache helper returns None on Redis errors.
        self.cache.get.side_effect = None
        self.cache.get.return_value = None
        self.cache.set.side_effect = None
        self.cache.set.return_value = None
        await self.download()

    async def test_failed_download_is_not_cached(self):
        obj = self.downloader()
        obj.run.side_effect = RuntimeError
        with self.assertRaises(RuntimeError):
            await cache_downloader(obj, self.remote).run()
        self.cache.set.assert_not_awaited()

    async def test_oversized_metadata_is_not_cached(self):
        with patch("pulp_maven.app.metadata_cache.MAX_METADATA_BYTES", 4):
            await self.download()
        self.cache.set.assert_not_awaited()

    async def test_retry_discards_partial_data(self):
        obj = self.downloader()
        fetch = obj.run.side_effect

        async def retry(extra_data=None):
            await obj.headers_ready_callback({})
            await obj.handle_data(b"partial")
            return await fetch(extra_data)

        obj.run.side_effect = retry
        await cache_downloader(obj, self.remote).run()
        await self.download(expected_fetch=False)
