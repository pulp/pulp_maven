# coding=utf-8
from urllib.parse import urljoin

from pulp_smash.pulp3.constants import (
    BASE_PUBLISHER_PATH,
    BASE_REMOTE_PATH,
    CONTENT_PATH
)

DOWNLOAD_POLICIES = ['on_demand']

MAVEN_CONTENT_NAME = 'maven.artifact'

MAVEN_CONTENT_PATH = urljoin(CONTENT_PATH, 'maven/artifact/')

MAVEN_REMOTE_PATH = urljoin(BASE_REMOTE_PATH, 'maven/maven/')

MAVEN_PUBLISHER_PATH = urljoin(BASE_PUBLISHER_PATH, 'maven/maven/')

MAVEN_FIXTURE_URL = 'https://repo1.maven.org/maven2/'
