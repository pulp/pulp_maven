import threading

_initialized = False
_init_lock = threading.Lock()
_cache_misses = None
_cache_evictions = None
_cache_full = None
_fallbacks = None
_load_duration = None


def _initialize():
    global _initialized, _cache_misses, _cache_evictions, _cache_full, _fallbacks, _load_duration
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        from django.conf import settings

        if settings.OTEL_ENABLED:
            from opentelemetry import metrics as otel_metrics

            meter = otel_metrics.get_meter("pulp-content")
            _cache_misses = meter.create_counter(
                name="maven_path_index_cache_misses_total",
                description="Maven path-index cache misses",
                unit="1",
            )
            # Watch eviction churn alongside misses to decide whether LRU needs a 2Q policy.
            _cache_evictions = meter.create_counter(
                name="maven_path_index_cache_evictions_total",
                description="Maven path-index cache evictions",
                unit="1",
            )
            _cache_full = meter.create_counter(
                name="maven_path_index_cache_full_total",
                description="Maven path-index cache-full failures",
                unit="1",
            )
            _fallbacks = meter.create_counter(
                name="maven_path_index_fallbacks_total",
                description="Maven path-index fallbacks",
                unit="1",
            )
            _load_duration = meter.create_histogram(
                name="maven_path_index_load_duration_seconds",
                description="Maven path-index view load duration",
                unit="s",
            )
        _initialized = True


def record_cache_miss(reason):
    _initialize()
    if _cache_misses is not None:
        _cache_misses.add(1, attributes={"reason": reason})


def record_cache_eviction():
    _initialize()
    if _cache_evictions is not None:
        _cache_evictions.add(1)


def record_cache_full():
    _initialize()
    if _cache_full is not None:
        _cache_full.add(1)


def record_fallback(reason):
    _initialize()
    if _fallbacks is not None:
        _fallbacks.add(1, attributes={"reason": reason})


def record_load_duration(duration, status):
    _initialize()
    if _load_duration is not None:
        _load_duration.record(duration, attributes={"status": status})
