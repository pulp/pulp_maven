"""Settings for the experimental, repository-opt-in path index integration."""

# Experimental: enable with pulp_labels["path_index"] = "true" on an S3-backed domain.
MAVEN_PATH_INDEX_CACHE_DIR = "/var/lib/pulp/path-index-cache"
MAVEN_PATH_INDEX_WORK_DIR = None
MAVEN_PATH_INDEX_CACHE_BYTES = 4 * 1024**3
MAVEN_PATH_INDEX_MAX_VIEWS = 8
MAVEN_PATH_INDEX_REFRESH_SECONDS = 2
# Match a hosted deployment's existing large-file redirect policy when configured.
MAVEN_PATH_INDEX_REDIRECT_THRESHOLD = None

# How long a deploy request waits, in seconds, for its turn to add the uploaded file to the
# repository while other deploys to the same repository go first. After that it answers 429.
MAVEN_DEPLOY_TASK_TIMEOUT = 30
