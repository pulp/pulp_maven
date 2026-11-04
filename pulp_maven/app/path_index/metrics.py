from django.conf import settings

if settings.OTEL_ENABLED:
    from opentelemetry import metrics as otel_metrics

    _meter = otel_metrics.get_meter("pulp-content")
    _cache_misses = _meter.create_counter(
        name="maven_path_index_cache_misses_total",
        description="Maven path-index cache misses",
        unit="1",
    )
    # Watch eviction churn alongside misses to decide whether LRU needs a 2Q policy.
    _cache_evictions = _meter.create_counter(
        name="maven_path_index_cache_evictions_total",
        description="Maven path-index cache evictions",
        unit="1",
    )
    _cache_full = _meter.create_counter(
        name="maven_path_index_cache_full_total",
        description="Maven path-index cache-full failures",
        unit="1",
    )
    _fallbacks = _meter.create_counter(
        name="maven_path_index_fallbacks_total",
        description="Maven path-index fallbacks",
        unit="1",
    )
else:
    _cache_misses = None
    _cache_evictions = None
    _cache_full = None
    _fallbacks = None


def record_cache_miss(reason):
    if _cache_misses is not None:
        _cache_misses.add(1, attributes={"reason": reason})


def record_cache_eviction():
    if _cache_evictions is not None:
        _cache_evictions.add(1)


def record_cache_full():
    if _cache_full is not None:
        _cache_full.add(1)


def record_fallback(reason):
    if _fallbacks is not None:
        _fallbacks.add(1, attributes={"reason": reason})
