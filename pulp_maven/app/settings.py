"""Plugin settings: access-policy conditions, and the experimental path index."""

DRF_ACCESS_POLICY = {
    "dynaconf_merge_unique": True,
    "reusable_conditions": ["pulp_maven.app.global_access_conditions"],
}

# Experimental: enable with pulp_labels["path_index"] = "true" on an S3-backed domain.
MAVEN_PATH_INDEX_CACHE_DIR = "/var/lib/pulp/path-index-cache"
MAVEN_PATH_INDEX_WORK_DIR = None
MAVEN_PATH_INDEX_CACHE_BYTES = 4 * 1024**3
MAVEN_PATH_INDEX_MAX_VIEWS = 8
MAVEN_PATH_INDEX_REFRESH_SECONDS = 2
# Match a hosted deployment's existing large-file redirect policy when configured.
MAVEN_PATH_INDEX_REDIRECT_THRESHOLD = None
