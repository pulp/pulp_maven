"""Redis-backed Bloom filter helpers."""

import logging
from uuid import uuid4

from django.conf import settings
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

log = logging.getLogger(__name__)

BLOOM_FILTER_CONFIG_LABEL = "pulp_maven.bloom"
BLOOM_FILTER_KEY_PREFIX = "pulp_maven:bloom_filter"
BLOOM_FILTER_BATCH_SIZE = 1000
BLOOM_FILTER_BUILD_TTL = 24 * 60 * 60
BLOOM_FILTER_REDIS_TIMEOUT = 1

_redis_connection = None


def new_bloom_filter_capacity(item_count):
    """Return a capacity with 50 percent headroom, rounded up."""
    return (item_count * 3 + 1) // 2


def bloom_filter_key(repository):
    """Return the Redis key for a repository's Bloom filter."""
    return f"{BLOOM_FILTER_KEY_PREFIX}:{repository.pulp_id}"


def bloom_filter_config_key(repository):
    """Return the Redis key storing the configuration used to build a filter."""
    return f"{bloom_filter_key(repository)}:config"


def get_redis_connection():
    """Return a fail-fast Redis connection for Bloom filter operations."""
    global _redis_connection

    if _redis_connection is None:
        redis_is_needed = (
            getattr(settings, "CACHE_ENABLED", None)
            or getattr(settings, "WORKER_TYPE", None) == "redis"
        )
        if not redis_is_needed:
            return None

        connection_kwargs = {
            "retry": Retry(NoBackoff(), retries=0),
            "socket_connect_timeout": BLOOM_FILTER_REDIS_TIMEOUT,
            "socket_timeout": BLOOM_FILTER_REDIS_TIMEOUT,
        }
        redis_url = getattr(settings, "REDIS_URL", None)
        if redis_url is not None:
            _redis_connection = Redis.from_url(redis_url, **connection_kwargs)
        else:
            _redis_connection = Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD,
                ssl=settings.REDIS_SSL,
                ssl_ca_certs=settings.REDIS_SSL_CA_CERTS,
                **connection_kwargs,
            )

    return _redis_connection


def parse_bloom_filter_config(repository, log_invalid=True):
    """Return a validated ``(capacity, error_rate)`` tuple, or ``None``."""
    config = repository.pulp_labels.get(BLOOM_FILTER_CONFIG_LABEL)
    if not config:
        return None

    try:
        capacity_text, error_rate_text = config.split(",")
        capacity = int(capacity_text)
        error_rate = float(error_rate_text)
        if capacity <= 0 or not 0 < error_rate < 1:
            raise ValueError
    except (TypeError, ValueError):
        if log_invalid:
            log.warning(
                "Bloom filter configuration %r for repository %s is invalid. Set the %s label "
                "to '<capacity>,<error-rate>', where capacity is greater than zero and the error "
                "rate is between zero and one. Bloom filtering is disabled for this repository.",
                config,
                repository.name,
                BLOOM_FILTER_CONFIG_LABEL,
            )
        return None

    return capacity, error_rate


def bloom_filter_might_contain(repository, repository_version, *paths):
    """Return whether the filter may contain any path, falling back safely on errors."""
    parsed_config = parse_bloom_filter_config(repository, log_invalid=False)
    if parsed_config is None:
        return True

    redis = get_redis_connection()
    if redis is None:
        return True

    try:
        capacity, error_rate = parsed_config
        expected_config = f"{capacity},{error_rate},{repository_version.number}"
        stored_config = redis.get(bloom_filter_config_key(repository))
        if isinstance(stored_config, bytes):
            stored_config = stored_config.decode()
        if stored_config != expected_config:
            return True
        matches = redis.execute_command("BF.MEXISTS", bloom_filter_key(repository), *paths)
        return any(matches)
    except (RedisError, TypeError, UnicodeError) as exc:
        log.warning(
            "Redis Bloom filter lookup failed for repository %s. Using the normal repository "
            "lookup instead: %s",
            repository.name,
            exc,
        )
        return True


def delete_bloom_filter(repository):
    """Delete a repository's filter and its stored configuration from Redis."""
    redis = get_redis_connection()
    if redis is None:
        return

    try:
        redis.delete(bloom_filter_key(repository), bloom_filter_config_key(repository))
    except (RedisError, TypeError) as exc:
        log.warning(
            "Could not delete the Redis Bloom filter for repository %s. Pulp will ignore the "
            "filter, but its Redis keys may remain: %s",
            repository.name,
            exc,
        )


def mark_not_ready_and_retrieve_config(redis, repository):
    """Prevent lookups from using a filter while it is being updated and return its configuration."""
    config_key = bloom_filter_config_key(repository)
    stored_config = redis.get(config_key)
    redis.delete(config_key)

    if isinstance(stored_config, bytes):
        stored_config = stored_config.decode()
    try:
        stored_config, version_number = stored_config.rsplit(",", 1)
        version_number = int(version_number)
    except (AttributeError, TypeError, ValueError):
        return None

    if redis.exists(bloom_filter_key(repository)):
        return stored_config, version_number
    return None


def add_paths_to_bloom_filter(redis, key, paths):
    """Add paths to a Redis Bloom filter in bounded batches."""
    batch = []
    for relative_path in paths:
        batch.append(relative_path)
        if len(batch) == BLOOM_FILTER_BATCH_SIZE:
            redis.execute_command("BF.MADD", key, *batch)
            batch.clear()
    if batch:
        redis.execute_command("BF.MADD", key, *batch)


def replace_bloom_filter(redis, repository, repository_version, capacity, error_rate, paths):
    """Build a Redis Bloom filter and atomically replace the repository's current filter."""
    key = bloom_filter_key(repository)
    config_key = bloom_filter_config_key(repository)
    config = f"{capacity},{error_rate},{repository_version.number}"
    temporary_key = f"{key}:building:{uuid4()}"

    try:
        redis.execute_command("BF.RESERVE", temporary_key, error_rate, capacity, "NONSCALING")
        redis.expire(temporary_key, BLOOM_FILTER_BUILD_TTL)
        add_paths_to_bloom_filter(redis, temporary_key, paths)
        redis.rename(temporary_key, key)
        redis.persist(key)
        redis.set(config_key, config)
    except Exception:
        try:
            redis.delete(temporary_key)
        except (RedisError, TypeError):
            pass
        raise
