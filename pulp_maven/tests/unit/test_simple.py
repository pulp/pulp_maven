from datetime import datetime, timezone
from pathlib import PurePath
from unittest import TestCase
from unittest.mock import MagicMock, patch

from pulp_maven.app.simple.utils import (
    MAVEN_SERIAL_CONSTANT,
    SIMPLE_API_VERSION,
    maven_content_to_download_info,
    write_simple_index,
    write_simple_index_json,
)


class TestWriteSimpleIndex(TestCase):
    def test_renders_project_names(self):
        html = write_simple_index(["com.example:lib-a", "org.test:lib-b"])
        self.assertIn('<a href="com.example:lib-a/">com.example:lib-a</a>', html)
        self.assertIn('<a href="org.test:lib-b/">org.test:lib-b</a>', html)

    def test_includes_api_version(self):
        html = write_simple_index(["com.example:lib"])
        self.assertIn(f'content="{SIMPLE_API_VERSION}"', html)

    def test_empty_project_list(self):
        html = write_simple_index([])
        self.assertIn("<title>Simple Index</title>", html)
        self.assertNotIn("<a ", html)

    def test_xss_is_escaped(self):
        html = write_simple_index(['<script>alert("xss")</script>'])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_streamed_returns_stream(self):
        result = write_simple_index(["com.example:lib"], streamed=True)
        joined = "".join(result)
        self.assertIn("com.example:lib", joined)


class TestWriteSimpleIndexJson(TestCase):
    def test_structure(self):
        result = write_simple_index_json(["com.example:a", "com.example:b"])
        self.assertEqual(result["meta"]["api-version"], SIMPLE_API_VERSION)
        self.assertEqual(result["meta"]["_last-serial"], MAVEN_SERIAL_CONSTANT)
        self.assertEqual(len(result["projects"]), 2)
        self.assertEqual(result["projects"][0]["name"], "com.example:a")
        self.assertEqual(result["projects"][1]["name"], "com.example:b")


class _FakeSettings:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestMavenContentToDownloadInfo(TestCase):
    def _make_artifact(self, **kwargs):
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        artifact_mock = MagicMock()
        artifact_mock.filename = kwargs.get("filename", "my-lib-1.0.jar")
        artifact_mock.group_id = kwargs.get("group_id", "com.example")
        artifact_mock.artifact_id = kwargs.get("artifact_id", "my-lib")
        artifact_mock.version = kwargs.get("version", "1.0.0")
        artifact_mock.pulp_created = kwargs.get("pulp_created", now)

        inner_artifact = MagicMock()
        inner_artifact.sha256 = kwargs.get("sha256", "abc123")
        inner_artifact.size = kwargs.get("size", 1024)

        ca = MagicMock()
        ca.artifact = inner_artifact
        artifact_mock.contentartifact_set.select_related.return_value.first.return_value = ca

        return artifact_mock

    def _default_settings(self, **overrides):
        defaults = {
            "CONTENT_ORIGIN": "https://pulp.example.com",
            "MAVEN_API_HOSTNAME": "",
            "CONTENT_PATH_PREFIX": "/pulp/content/",
        }
        defaults.update(overrides)
        return _FakeSettings(**defaults)

    def test_builds_url_without_domain(self):
        fake = self._default_settings()
        with patch("pulp_maven.app.simple.utils.settings", fake):
            artifact = self._make_artifact()
            result = maven_content_to_download_info(artifact, "my-repo")

        self.assertEqual(
            result["url"], "https://pulp.example.com/pulp/content/my-repo/my-lib-1.0.jar"
        )
        self.assertEqual(result["filename"], "my-lib-1.0.jar")
        self.assertEqual(result["digests"], {"sha256": "abc123"})
        self.assertEqual(result["size"], 1024)
        self.assertEqual(result["group_id"], "com.example")
        self.assertEqual(result["artifact_id"], "my-lib")
        self.assertEqual(result["version"], "1.0.0")

    def test_builds_url_with_domain(self):
        fake = self._default_settings()
        with patch("pulp_maven.app.simple.utils.settings", fake):
            domain = MagicMock()
            domain.name = "acme"
            artifact = self._make_artifact()
            result = maven_content_to_download_info(artifact, "my-repo", domain=domain)

        self.assertEqual(
            result["url"],
            "https://pulp.example.com/pulp/content/acme/my-repo/my-lib-1.0.jar",
        )

    def test_missing_content_artifact(self):
        fake = self._default_settings()
        with patch("pulp_maven.app.simple.utils.settings", fake):
            artifact = self._make_artifact()
            artifact.contentartifact_set.select_related.return_value.first.return_value = None
            result = maven_content_to_download_info(artifact, "my-repo")

        self.assertEqual(result["digests"], {"sha256": ""})
        self.assertIsNone(result["size"])

    def test_fallback_to_maven_api_hostname(self):
        fake = self._default_settings(
            CONTENT_ORIGIN="",
            MAVEN_API_HOSTNAME="https://maven.example.com",
            CONTENT_PATH_PREFIX="/content/",
        )
        with patch("pulp_maven.app.simple.utils.settings", fake):
            artifact = self._make_artifact(filename="app.jar")
            result = maven_content_to_download_info(artifact, "repo")

        self.assertTrue(result["url"].startswith("https://maven.example.com/"))


class TestMetadataPathParsing(TestCase):
    """Test the path parsing logic used in MetadataView.retrieve."""

    @staticmethod
    def _parse_meta(meta):
        meta_path = PurePath(meta)
        package = None
        version = None
        if meta_path.match("*/*/json"):
            package = meta_path.parts[0]
            version = meta_path.parts[1]
        elif meta_path.match("*/json"):
            package = meta_path.parts[0]
        return package, version

    def test_package_only(self):
        package, version = self._parse_meta("com.example:my-lib/json")
        self.assertEqual(package, "com.example:my-lib")
        self.assertIsNone(version)

    def test_package_with_version(self):
        package, version = self._parse_meta("com.example:my-lib/1.0.0/json")
        self.assertEqual(package, "com.example:my-lib")
        self.assertEqual(version, "1.0.0")

    def test_trailing_slash(self):
        package, version = self._parse_meta("com.example:my-lib/json/")
        self.assertEqual(package, "com.example:my-lib")
        self.assertIsNone(version)

    def test_no_json_suffix(self):
        package, version = self._parse_meta("com.example:my-lib/1.0.0")
        self.assertIsNone(package)
        self.assertIsNone(version)

    def test_empty_string(self):
        package, version = self._parse_meta("")
        self.assertIsNone(package)
        self.assertIsNone(version)
