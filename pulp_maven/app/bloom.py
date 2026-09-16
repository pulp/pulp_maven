"""Redis-backed Bloom filter helpers."""

import logging
from uuid import uuid4

from redis.exceptions import RedisError

log = logging.getLogger(__name__)

BLOOM_FILTER_CONFIG_LABEL = "pulp_maven.bloom"
BLOOM_FILTER_KEY_PREFIX = "pulp_maven:bloom_filter"
BLOOM_FILTER_BATCH_SIZE = 1000
BLOOM_FILTER_BUILD_TTL = 24 * 60 * 60


def grown_bloom_filter_capacity(item_count):
    """Return a capacity with 50 percent headroom, rounded up."""
    return (item_count * 3 + 1) // 2


def bloom_filter_key(repository):
    """Return the Redis key for a repository's Bloom filter."""
    return f"{BLOOM_FILTER_KEY_PREFIX}:{repository.pulp_id}"


def bloom_filter_config_key(repository):
    """Return the Redis key storing the configuration used to build a filter."""
    return f"{bloom_filter_key(repository)}:config"


def get_redis_connection():
    """Return Pulpcore's configured synchronous Redis connection."""
    from pulpcore.plugin.cache import SyncContentCache

    return SyncContentCache().redis


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
            log.warning("Invalid Bloom filter configuration for repository %s", repository.name)
        return None

    return capacity, error_rate


def bloom_filter_might_contain(repository, *paths):
    """Return whether the filter may contain any path, falling back safely on errors."""
    parsed_config = parse_bloom_filter_config(repository, log_invalid=False)
    if parsed_config is None:
        return True

    redis = get_redis_connection()
    if redis is None:
        return True

    try:
        capacity, error_rate = parsed_config
        expected_config = f"{capacity},{error_rate}"
        stored_config = redis.get(bloom_filter_config_key(repository))
        if isinstance(stored_config, bytes):
            stored_config = stored_config.decode()
        if stored_config != expected_config:
            return True
        matches = redis.execute_command("BF.MEXISTS", bloom_filter_key(repository), *paths)
    except (RedisError, TypeError):
        log.debug("Unable to query Redis Bloom filter", exc_info=True)
        return True
    return any(matches)


def delete_bloom_filter(repository):
    """Delete a repository's filter and its stored configuration from Redis."""
    redis = get_redis_connection()
    if redis is None:
        return

    try:
        redis.delete(bloom_filter_key(repository), bloom_filter_config_key(repository))
    except (RedisError, TypeError):
        log.warning("Unable to delete Redis Bloom filter for repository %s", repository.name)


def redis_bloom_filter_matches_config(redis, repository, config):
    """Return whether Redis has a filter built with the requested configuration."""
    key = bloom_filter_key(repository)
    stored_config = redis.get(bloom_filter_config_key(repository))
    if isinstance(stored_config, bytes):
        stored_config = stored_config.decode()
    return stored_config == config and bool(redis.exists(key))


def mark_bloom_filter_not_ready(redis, repository):
    """Prevent lookups from using a filter while it is being updated."""
    redis.delete(bloom_filter_config_key(repository))


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


def replace_bloom_filter(redis, repository, capacity, error_rate, paths):
    """Build a Redis Bloom filter and atomically replace the repository's current filter."""
    key = bloom_filter_key(repository)
    config_key = bloom_filter_config_key(repository)
    config = f"{capacity},{error_rate}"
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
