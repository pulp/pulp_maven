from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from redis.exceptions import ConnectionError

from pulp_maven.app.bloom import (
    BLOOM_FILTER_BUILD_TTL,
    BLOOM_FILTER_CONFIG_LABEL,
    add_paths_to_bloom_filter,
    bloom_filter_key,
    bloom_filter_might_contain,
    grown_bloom_filter_capacity,
    mark_bloom_filter_not_ready,
    parse_bloom_filter_config,
    replace_bloom_filter,
)


def repository_with_config(value=None):
    """Build repository state without touching the database."""
    labels = {BLOOM_FILTER_CONFIG_LABEL: value} if value else {}
    return SimpleNamespace(pulp_id=uuid4(), pulp_labels=labels, name="test")


class TestNothing(TestCase):
    """Test Nothing (placeholder)."""

    def test_nothing_at_all(self):
        """Test that the tests are running and that's it."""
        self.assertTrue(True)


class TestBloomFilterLookup(TestCase):
    """Test Redis Bloom filter lookups."""

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_returns_true_when_any_path_might_exist(self, get_redis_connection):
        repository = repository_with_config("1000,0.01")
        redis = get_redis_connection.return_value
        redis.get.return_value = b"1000,0.01"
        redis.execute_command.return_value = [0, 1]

        result = bloom_filter_might_contain(repository, "missing.jar", "index.html")

        self.assertTrue(result)
        redis.execute_command.assert_called_once_with(
            "BF.MEXISTS",
            bloom_filter_key(repository),
            "missing.jar",
            "index.html",
        )

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_returns_false_when_all_paths_are_absent(self, get_redis_connection):
        repository = repository_with_config("1000,0.01")
        redis = get_redis_connection.return_value
        redis.get.return_value = "1000,0.01"
        redis.execute_command.return_value = [0, 0]

        self.assertFalse(bloom_filter_might_contain(repository, "one", "two"))

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_skips_redis_without_configuration(self, get_redis_connection):
        repository = repository_with_config()

        self.assertTrue(bloom_filter_might_contain(repository, "missing.jar"))
        get_redis_connection.assert_not_called()

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_falls_back_when_redis_is_unavailable(self, get_redis_connection):
        repository = repository_with_config("1000,0.01")
        redis = get_redis_connection.return_value
        redis.get.return_value = "1000,0.01"
        redis.execute_command.side_effect = ConnectionError

        self.assertTrue(bloom_filter_might_contain(repository, "missing.jar"))

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_falls_back_while_filter_is_being_updated(self, get_redis_connection):
        repository = repository_with_config("1000,0.01")
        redis = get_redis_connection.return_value
        redis.get.return_value = None

        self.assertTrue(bloom_filter_might_contain(repository, "missing.jar"))
        redis.execute_command.assert_not_called()


class TestBloomFilterConfig(TestCase):
    def test_grows_capacity_by_half_and_rounds_up(self):
        self.assertEqual(grown_bloom_filter_capacity(100_000), 150_000)
        self.assertEqual(grown_bloom_filter_capacity(100_001), 150_002)

    def test_parses_valid_config(self):
        repository = repository_with_config("1000,0.0001")

        self.assertEqual(parse_bloom_filter_config(repository), (1000, 0.0001))

    def test_rejects_invalid_config(self):
        for config in ("invalid", "0,0.1", "100,0", "100,1"):
            with self.subTest(config=config):
                repository = repository_with_config(config)
                self.assertIsNone(parse_bloom_filter_config(repository))


class TestBloomFilterUpdates(TestCase):
    def test_marks_filter_not_ready(self):
        repository = repository_with_config("1000,0.01")
        redis = Mock()

        mark_bloom_filter_not_ready(redis, repository)

        redis.delete.assert_called_once_with(f"{bloom_filter_key(repository)}:config")

    def test_adds_paths_in_batches(self):
        redis = Mock()
        paths = (str(index) for index in range(1001))

        add_paths_to_bloom_filter(redis, "filter", paths)

        self.assertEqual(redis.execute_command.call_count, 2)
        first_call, second_call = redis.execute_command.call_args_list
        self.assertEqual(first_call.args[:2], ("BF.MADD", "filter"))
        self.assertEqual(len(first_call.args[2:]), 1000)
        self.assertEqual(second_call.args, ("BF.MADD", "filter", "1000"))

    @patch("pulp_maven.app.bloom.uuid4", return_value=UUID(int=0))
    def test_builds_then_atomically_replaces_filter(self, uuid4):
        repository = repository_with_config("1000,0.01")
        redis = Mock()
        key = bloom_filter_key(repository)
        temporary_key = f"{key}:building:{UUID(int=0)}"

        replace_bloom_filter(redis, repository, 1000, 0.01, ["one", "two"])

        redis.execute_command.assert_any_call("BF.RESERVE", temporary_key, 0.01, 1000, "NONSCALING")
        redis.execute_command.assert_any_call("BF.MADD", temporary_key, "one", "two")
        redis.expire.assert_called_once_with(temporary_key, BLOOM_FILTER_BUILD_TTL)
        redis.rename.assert_called_once_with(temporary_key, key)
        redis.persist.assert_called_once_with(key)
        redis.set.assert_called_once_with(f"{key}:config", "1000,0.01")

    @patch("pulp_maven.app.bloom.uuid4", return_value=UUID(int=0))
    def test_removes_temporary_filter_when_build_fails(self, uuid4):
        repository = repository_with_config("1000,0.01")
        redis = Mock()
        key = bloom_filter_key(repository)
        temporary_key = f"{key}:building:{UUID(int=0)}"
        redis.execute_command.side_effect = [True, ConnectionError("failed")]

        with self.assertRaises(ConnectionError):
            replace_bloom_filter(redis, repository, 1000, 0.01, ["one"])

        redis.delete.assert_called_once_with(temporary_key)
        redis.rename.assert_not_called()


class TestMavenRemoteExcludeGroupIds(TestCase):
    """Test group exclusion for pull-through URLs."""

    @staticmethod
    def remote(*group_ids):
        from pulp_maven.app.models import MavenRemote

        # An explicit domain id keeps pulpcore from querying the DB for the default domain.
        return MavenRemote(
            url="https://repo.example/maven2/",
            exclude_group_ids=list(group_ids),
            pulp_domain_id=uuid4(),
        )

    def test_no_exclusions_returns_url(self):
        remote = self.remote()
        url = remote.get_remote_artifact_url("com/a/b/1.0/b-1.0.jar")
        self.assertEqual(url, "https://repo.example/maven2/com/a/b/1.0/b-1.0.jar")

    def test_excluded_paths_return_none(self):
        remote = self.remote("com.example")
        for rel in (
            "com/example/lib/1.0/lib-1.0.jar",
            "com/example/lib/1.0/lib-1.0.pom",
            "com/example/lib/maven-metadata.xml",
            "com/example/lib/maven-metadata.xml.sha1",
            "com/example/lib/1.0-SNAPSHOT/maven-metadata.xml.md5",
            "com/example/sub/deep/lib/1.0/lib-1.0.jar",
            "com/example",
        ):
            self.assertIsNone(remote.get_remote_artifact_url(rel), rel)

    def test_unnormalized_paths_are_still_excluded(self):
        remote = self.remote("com.example")
        for rel in (
            "/com/example/lib/1.0/lib-1.0.jar",
            "com//example/lib/1.0/lib-1.0.jar",
            "com/./example/lib/1.0/lib-1.0.jar",
            "foo/../com/example/lib/1.0/lib-1.0.jar",
            "com/other/../example/lib/maven-metadata.xml",
        ):
            self.assertIsNone(remote.get_remote_artifact_url(rel), rel)

    def test_dotdot_out_of_excluded_group_is_allowed(self):
        remote = self.remote("com.example")
        rel = "com/example/../other/lib/1.0/lib-1.0.jar"
        self.assertIsNotNone(remote.get_remote_artifact_url(rel))

    def test_similar_group_ids_not_excluded(self):
        remote = self.remote("com.example")
        for rel in (
            "com/example2/lib/1.0/lib-1.0.jar",
            "com/examples/lib/maven-metadata.xml",
            "com/other/example/lib/1.0/lib-1.0.jar",
            "org/com/example/lib/1.0/lib-1.0.jar",
        ):
            self.assertIsNotNone(remote.get_remote_artifact_url(rel), rel)

    def test_multiple_groups(self):
        remote = self.remote("com.one", "org.two")
        self.assertIsNone(remote.get_remote_artifact_url("org/two/x/1/x-1.jar"))
        self.assertIsNone(remote.get_remote_artifact_url("com/one/x/1/x-1.jar"))
        self.assertIsNotNone(remote.get_remote_artifact_url("org/three/x/1/x-1.jar"))

    def test_blank_entries_ignored(self):
        remote = self.remote("", ".")
        self.assertIsNotNone(remote.get_remote_artifact_url("com/a/b/1/b-1.jar"))
