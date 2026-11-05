"""Bounded Redis caching for streamed upstream Maven metadata."""

import hashlib
import json

MAX_METADATA_BYTES = 4 * 1024 * 1024


def is_metadata_path(path):
    filename = path.rsplit("/", 1)[-1]
    return filename == "maven-metadata.xml" or filename in {
        f"maven-metadata.xml.{digest}"
        for digest in ("md5", "sha1", "sha224", "sha256", "sha384", "sha512")
    }


def get_cache():
    from pulpcore.plugin.cache import AsyncContentCache

    return AsyncContentCache()


def cache_downloader(downloader, remote):
    """Wrap streaming downloads without creating persistent Pulp content."""
    identity = (
        f"{remote.pulp_domain_id}:{remote.pk}:{remote.pulp_last_updated}:"
        f"{remote.metadata_cache_ttl}:{downloader.url}"
    )
    key = "pulp_maven:metadata:v1:" + hashlib.sha256(identity.encode()).hexdigest()
    ttl = remote.metadata_cache_ttl
    original_run = downloader.run

    async def run(extra_data=None):
        cache = get_cache()
        cached = await cache.get("response", base_key=key)
        if cached is not None:
            try:
                value = json.loads(cached)
                body = bytes.fromhex(value["body"])
                headers = value["headers"]
            except (ValueError, KeyError, TypeError):
                pass
            else:
                await downloader.headers_ready_callback(headers)
                await downloader.handle_data(body)
                await downloader.finalize()
                return None

        original_data = downloader.handle_data
        original_headers = downloader.headers_ready_callback
        body = bytearray()
        headers = {}

        async def handle_headers(value):
            nonlocal headers, body
            body = bytearray()  # Discard partial data on retries.
            headers = dict(value)
            await original_headers(value)

        async def handle_data(data):
            nonlocal body
            if body is not None:
                if len(body) + len(data) > MAX_METADATA_BYTES:
                    body = None
                else:
                    body.extend(data)
            await original_data(data)

        downloader.handle_data = handle_data
        downloader.headers_ready_callback = handle_headers
        try:
            result = await original_run(extra_data=extra_data)
            if body is not None:
                await cache.set(
                    "response",
                    json.dumps({"body": body.hex(), "headers": headers}),
                    expires=ttl,
                    base_key=key,
                )
            return result
        finally:
            downloader.handle_data = original_data
            downloader.headers_ready_callback = original_headers

    downloader.run = run
    return downloader
