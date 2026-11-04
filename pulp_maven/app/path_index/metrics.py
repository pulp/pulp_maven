from django.conf import settings

if settings.OTEL_ENABLED:
    from pulpcore.metrics import init_otel_meter

    _meter = init_otel_meter("pulp-content")
    _load_duration = _meter.create_histogram(
        name="maven_path_index_load_duration_seconds",
        description="Maven path-index view load duration",
        unit="s",
    )
    _fallbacks = _meter.create_counter(
        name="maven_path_index_fallbacks_total",
        description="Maven requests that fall back from the path index",
        unit="1",
    )
    _prewarm_scans = _meter.create_counter(
        name="maven_path_index_prewarm_scans_total",
        description="Path-index prewarm scans",
        unit="1",
    )
    _prewarm_scan_duration = _meter.create_histogram(
        name="maven_path_index_prewarm_scan_duration_seconds",
        description="Path-index prewarm scan duration",
        unit="s",
    )
    _prewarm_keys_scheduled = _meter.create_counter(
        name="maven_path_index_prewarm_keys_scheduled_total",
        description="Path-index views scheduled for prewarming",
        unit="1",
    )
else:
    _load_duration = None
    _fallbacks = None
    _prewarm_scans = None
    _prewarm_scan_duration = None
    _prewarm_keys_scheduled = None


def record_load_duration(duration, *, success):
    if _load_duration is not None:
        _load_duration.record(duration, attributes={"status": "success" if success else "failure"})


def record_fallback(reason):
    if _fallbacks is not None:
        _fallbacks.add(1, attributes={"reason": reason})


def record_prewarm_scan(duration, scheduled):
    if _prewarm_scans is not None:
        _prewarm_scans.add(1)
        _prewarm_scan_duration.record(duration)
        _prewarm_keys_scheduled.add(scheduled)
