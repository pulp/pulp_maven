from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

from redis.exceptions import ConnectionError

from pulp_maven.app import bloom
from pulp_maven.app.bloom import (
    BLOOM_FILTER_BUILD_TTL,
    BLOOM_FILTER_CONFIG_LABEL,
    BLOOM_FILTER_REDIS_TIMEOUT,
    add_paths_to_bloom_filter,
    bloom_filter_key,
    bloom_filter_might_contain,
    delete_bloom_filter,
    get_redis_connection,
    mark_not_ready_and_retrieve_config,
    new_bloom_filter_capacity,
    parse_bloom_filter_config,
    replace_bloom_filter,
)


def repository_with_config(value=None):
    """Build repository state without touching the database."""
    labels = {BLOOM_FILTER_CONFIG_LABEL: value} if value else {}
    return SimpleNamespace(pulp_id=uuid4(), pulp_labels=labels, name="test")


def repository_version(number=1):
    """Build repository version state without touching the database."""
    return SimpleNamespace(number=number)


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
        redis.get.return_value = b"1000,0.01,1"
        redis.execute_command.return_value = [0, 1]

        result = bloom_filter_might_contain(
            repository, repository_version(), "missing.jar", "index.html"
        )

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
        redis.get.return_value = "1000,0.01,1"
        redis.execute_command.return_value = [0, 0]

        self.assertFalse(bloom_filter_might_contain(repository, repository_version(), "one", "two"))

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_skips_redis_without_configuration(self, get_redis_connection):
        repository = repository_with_config()

        self.assertTrue(bloom_filter_might_contain(repository, repository_version(), "missing.jar"))
        get_redis_connection.assert_not_called()

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_falls_back_when_redis_is_unavailable(self, get_redis_connection):
        repository = repository_with_config("1000,0.01")
        redis = get_redis_connection.return_value
        redis.get.return_value = "1000,0.01,1"
        redis.execute_command.side_effect = ConnectionError

        self.assertTrue(bloom_filter_might_contain(repository, repository_version(), "missing.jar"))

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_falls_back_when_redis_returns_an_invalid_response(self, get_redis_connection):
        repository = repository_with_config("1000,0.01")
        redis = get_redis_connection.return_value
        redis.get.return_value = "1000,0.01,1"
        redis.execute_command.return_value = None

        self.assertTrue(bloom_filter_might_contain(repository, repository_version(), "missing.jar"))

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_falls_back_when_filter_is_for_an_older_repository_version(self, get_redis_connection):
        repository = repository_with_config("1000,0.01")
        redis = get_redis_connection.return_value
        redis.get.return_value = "1000,0.01,1"

        self.assertTrue(
            bloom_filter_might_contain(repository, repository_version(2), "new-content.jar")
        )
        redis.execute_command.assert_not_called()

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_falls_back_while_filter_is_being_updated(self, get_redis_connection):
        repository = repository_with_config("1000,0.01")
        redis = get_redis_connection.return_value
        redis.get.return_value = None

        self.assertTrue(bloom_filter_might_contain(repository, repository_version(), "missing.jar"))
        redis.execute_command.assert_not_called()


class TestBloomFilterConnection(TestCase):
    def tearDown(self):
        bloom._redis_connection = None

    @patch("pulp_maven.app.bloom.Redis")
    def test_creates_and_reuses_a_fail_fast_connection(self, redis_class):
        settings = SimpleNamespace(
            CACHE_ENABLED=True,
            WORKER_TYPE="db",
            REDIS_URL=None,
            REDIS_HOST="redis-bloom",
            REDIS_PORT=6379,
            REDIS_DB=0,
            REDIS_PASSWORD="password",
            REDIS_SSL=False,
            REDIS_SSL_CA_CERTS=None,
        )

        with patch.dict(bloom.__dict__, {"settings": settings}):
            first_connection = get_redis_connection()
            second_connection = get_redis_connection()

        self.assertIs(first_connection, second_connection)
        redis_class.assert_called_once()
        connection_kwargs = redis_class.call_args.kwargs
        self.assertEqual(connection_kwargs["socket_connect_timeout"], BLOOM_FILTER_REDIS_TIMEOUT)
        self.assertEqual(connection_kwargs["socket_timeout"], BLOOM_FILTER_REDIS_TIMEOUT)
        self.assertEqual(connection_kwargs["retry"]._retries, 0)


class TestBloomFilterConfig(TestCase):
    def test_grows_capacity_by_half_and_rounds_up(self):
        self.assertEqual(new_bloom_filter_capacity(100_000), 150_000)
        self.assertEqual(new_bloom_filter_capacity(100_001), 150_002)

    def test_parses_valid_config(self):
        repository = repository_with_config("1000,0.0001")

        self.assertEqual(parse_bloom_filter_config(repository), (1000, 0.0001))

    def test_rejects_invalid_config(self):
        for config in ("invalid", "0,0.1", "100,0", "100,1"):
            with self.subTest(config=config):
                repository = repository_with_config(config)
                self.assertIsNone(parse_bloom_filter_config(repository))

    def test_invalid_config_message_explains_the_expected_format(self):
        repository = repository_with_config("invalid")

        with self.assertLogs("pulp_maven.app.bloom", level="WARNING") as logs:
            self.assertIsNone(parse_bloom_filter_config(repository))

        self.assertIn("'<capacity>,<error-rate>'", logs.output[0])
        self.assertIn("Bloom filtering is disabled", logs.output[0])


class TestBloomFilterUpdates(TestCase):
    def test_marks_filter_not_ready_and_returns_its_config(self):
        repository = repository_with_config("1000,0.01")
        redis = Mock()
        redis.get.return_value = b"1000,0.01,4"
        redis.exists.return_value = 1
        config_key = f"{bloom_filter_key(repository)}:config"

        result = mark_not_ready_and_retrieve_config(redis, repository)

        self.assertEqual(result, ("1000,0.01", 4))
        redis.get.assert_called_once_with(config_key)
        redis.delete.assert_called_once_with(config_key)
        redis.exists.assert_called_once_with(bloom_filter_key(repository))

    def test_legacy_filter_config_is_marked_not_ready(self):
        repository = repository_with_config("1000,0.01")
        redis = Mock()
        redis.get.return_value = "1000,0.01"
        config_key = f"{bloom_filter_key(repository)}:config"

        self.assertIsNone(mark_not_ready_and_retrieve_config(redis, repository))
        redis.delete.assert_called_once_with(config_key)
        redis.exists.assert_not_called()

    @patch("pulp_maven.app.bloom.get_redis_connection")
    def test_delete_does_not_fail_when_redis_is_unavailable(self, get_redis_connection):
        repository = repository_with_config("1000,0.01")
        redis = get_redis_connection.return_value
        redis.delete.side_effect = ConnectionError("connection refused")

        with self.assertLogs("pulp_maven.app.bloom", level="WARNING") as logs:
            delete_bloom_filter(repository)

        self.assertIn("Pulp will ignore the filter", logs.output[0])

    def test_returns_none_when_filter_is_missing(self):
        repository = repository_with_config("1000,0.01")
        redis = Mock()
        redis.get.return_value = "1000,0.01,1"
        redis.exists.return_value = 0

        result = mark_not_ready_and_retrieve_config(redis, repository)

        self.assertIsNone(result)
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

        replace_bloom_filter(redis, repository, repository_version(), 1000, 0.01, ["one", "two"])

        redis.execute_command.assert_any_call("BF.RESERVE", temporary_key, 0.01, 1000, "NONSCALING")
        redis.execute_command.assert_any_call("BF.MADD", temporary_key, "one", "two")
        redis.expire.assert_called_once_with(temporary_key, BLOOM_FILTER_BUILD_TTL)
        redis.rename.assert_called_once_with(temporary_key, key)
        redis.persist.assert_called_once_with(key)
        redis.set.assert_called_once_with(f"{key}:config", "1000,0.01,1")

    @patch("pulp_maven.app.bloom.uuid4", return_value=UUID(int=0))
    def test_removes_temporary_filter_when_build_fails(self, uuid4):
        repository = repository_with_config("1000,0.01")
        redis = Mock()
        key = bloom_filter_key(repository)
        temporary_key = f"{key}:building:{UUID(int=0)}"
        redis.execute_command.side_effect = [True, ConnectionError("failed")]

        with self.assertRaises(ConnectionError):
            replace_bloom_filter(redis, repository, repository_version(), 1000, 0.01, ["one"])

        redis.delete.assert_called_once_with(temporary_key)
        redis.rename.assert_not_called()
