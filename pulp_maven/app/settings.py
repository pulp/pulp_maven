"""Settings for the experimental, repository-opt-in path index integration."""

# Experimental: enable with pulp_labels["path_index"] = "true" on an S3-backed domain.
MAVEN_PATH_INDEX_CACHE_DIR = "/var/lib/pulp/path-index-cache"
MAVEN_PATH_INDEX_WORK_DIR = None
MAVEN_PATH_INDEX_CACHE_BYTES = 4 * 1024**3
# Tune with maven_path_index_cache_misses_total, _cache_evictions_total, and _cache_full_total.
MAVEN_PATH_INDEX_MAX_VIEWS = 8
MAVEN_PATH_INDEX_REFRESH_SECONDS = 2
MAVEN_PATH_INDEX_PREWARM_INTERVAL_SECONDS = 60
MAVEN_PATH_INDEX_PREWARM_JITTER_SECONDS = 15
# Match a hosted deployment's existing large-file redirect policy when configured.
MAVEN_PATH_INDEX_REDIRECT_THRESHOLD = None
