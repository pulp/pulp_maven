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
