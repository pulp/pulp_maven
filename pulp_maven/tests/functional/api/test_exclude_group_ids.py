"""Tests that excluded groupIds never reach the upstream during pull-through."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

import pytest
from aiohttp import ClientResponseError

from pulp_maven.tests.functional.utils import download_file

POM = b"<project><modelVersion>4.0.0</modelVersion></project>"


class _Upstream(ThreadingHTTPServer):
    """A tiny Maven upstream that serves a POM for any .pom path and records all requests."""

    def __init__(self):
        self.requests = []
        super().__init__(("0.0.0.0", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        if self.path.endswith(".pom"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(POM)))
            self.end_headers()
            self.wfile.write(POM)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


@pytest.fixture
def upstream():
    server = _Upstream()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Address of this host as seen by a Pulp running in a container or locally.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            host = sock.getsockname()[0]
        server.url = f"http://{host}:{server.server_address[1]}/"
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_excluded_group_ids_skip_upstream(
    upstream,
    maven_distribution_factory,
    maven_remote_factory,
    maven_repo_factory,
    distribution_base_url,
):
    remote = maven_remote_factory(url=upstream.url, exclude_group_ids=["com.example"])
    repository = maven_repo_factory(remote=remote.pulp_href)
    distribution = maven_distribution_factory(
        remote=remote.pulp_href, repository=repository.pulp_href
    )
    base_url = distribution_base_url(distribution.base_url)

    excluded = [
        "com/example/lib/1.0/lib-1.0.pom",
        "com/example/sub/lib/1.0/lib-1.0.pom",
        "com/example/lib/maven-metadata.xml",
        "com/example/lib/maven-metadata.xml.sha1",
    ]
    for rel in excluded:
        with pytest.raises(ClientResponseError) as exc:
            download_file(urljoin(base_url, rel))
        assert exc.value.status == 404, rel
    assert upstream.requests == []

    # Similar-looking groups and unrelated groups are still pulled through.
    for rel in ("com/example2/lib/1.0/lib-1.0.pom", "org/other/lib/1.0/lib-1.0.pom"):
        assert download_file(urljoin(base_url, rel)).response_obj.status == 200, rel
    assert sorted(upstream.requests) == [
        "/com/example2/lib/1.0/lib-1.0.pom",
        "/org/other/lib/1.0/lib-1.0.pom",
    ]
