"""Constants for Pulp Maven plugin tests."""
from urllib.parse import urljoin

from pulp_smash.pulp3.constants import (
    BASE_CONTENT_PATH,
    BASE_DISTRIBUTION_PATH,
    BASE_PUBLISHER_PATH,
    BASE_REMOTE_PATH,
    BASE_REPO_PATH,
)

DOWNLOAD_POLICIES = ["on_demand"]

MAVEN_CONTENT_NAME = "maven.artifact"

MAVEN_CONTENT_PATH = urljoin(BASE_CONTENT_PATH, "maven/artifact/")

MAVEN_DISTRIBUTION_PATH = urljoin(BASE_DISTRIBUTION_PATH, "maven/maven/")

MAVEN_REMOTE_PATH = urljoin(BASE_REMOTE_PATH, "maven/maven/")

MAVEN_REPO_PATH = urljoin(BASE_REPO_PATH, "maven/maven/")

MAVEN_PUBLISHER_PATH = urljoin(BASE_PUBLISHER_PATH, "maven/maven/")

MAVEN_FIXTURE_URL = "https://repo1.maven.org/maven2/"
