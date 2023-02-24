"""Tests that verify download of content served by Pulp."""
import hashlib
from urllib.parse import urljoin

from pulp_maven.tests.functional.utils import download_file


def test_download_content(
    maven_distribution_factory, maven_remote_factory, maven_artifact_api_client
):
    """Verify whether content served by pulp can be downloaded.

    The process of creating a Maven mirror is:

    1. Create a Maven Remote with a URL pointing to the root of a Maven repository.
    2. Create a distribution with the remote set HREF from 1.

    Do the following:

    1. Create a Maven Remote and a Distribution.
    2. Select a random content unit in the distribution. Download that
       content unit from Pulp, and verify that the content unit has the
       same checksum when fetched directly from Maven Central.
    """
    remote = maven_remote_factory(url="https://repo1.maven.org/maven2/")
    distribution = maven_distribution_factory(remote=remote.pulp_href)

    # Pick a content unit, and download it from the remote repository
    unit_path = "academy/alex/custommatcher/1.0/custommatcher-1.0-javadoc.jar.sha1"
    remote_unit_url = urljoin(remote.url, unit_path)
    downloaded_file = download_file(remote_unit_url)
    remote_unit_checksum = hashlib.sha256(downloaded_file.body).hexdigest()

    # And from Pulp
    pulp_unit_url = urljoin(distribution.base_url, unit_path)
    downloaded_file = download_file(pulp_unit_url)
    pulp_unit_checksum = hashlib.sha256(downloaded_file.body).hexdigest()

    assert remote_unit_checksum == pulp_unit_checksum

    # Check that Pulp created a MavenArtifact
    content_response = maven_artifact_api_client.list(filename="custommatcher-1.0-javadoc.jar.sha1")
    assert content_response.count == 1
