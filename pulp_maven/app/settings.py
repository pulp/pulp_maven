"""Settings for the experimental, repository-opt-in path index integration."""

# Experimental: repositories must also opt in with pulp_labels["path_index"] = "true".
MAVEN_PATH_INDEX_MODE = "off"
MAVEN_PATH_INDEX_S3_BUCKET = ""
MAVEN_PATH_INDEX_S3_PREFIX = "maven-path-index"
MAVEN_PATH_INDEX_S3_ENDPOINT = None
MAVEN_PATH_INDEX_S3_REGION = None
MAVEN_PATH_INDEX_CACHE_DIR = "/var/lib/pulp/path-index-cache"
MAVEN_PATH_INDEX_WORK_DIR = None
MAVEN_PATH_INDEX_CACHE_BYTES = 4 * 1024**3
MAVEN_PATH_INDEX_MAX_VIEWS = 8
MAVEN_PATH_INDEX_REFRESH_SECONDS = 2
MAVEN_PATH_INDEX_BUILD_WORKERS = 2
# Match a hosted deployment's existing large-file redirect policy when configured.
MAVEN_PATH_INDEX_REDIRECT_THRESHOLD = None
