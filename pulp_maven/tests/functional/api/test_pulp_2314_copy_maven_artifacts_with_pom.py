"""Regression test for PULP-2314.

Copying maven artifacts that include a .pom file fails because _ensure_packages()
calls new_version.add_content(MavenPackage...) and the domain assertion in
add_content raises AssertionError.
"""

import os
import tempfile

import pytest


@pytest.mark.parallel
def test_copy_artifacts_with_pom_succeeds(
    maven_artifact_api_client,
    maven_package_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
):
    """PULP-2314: copying maven artifacts that include a .pom must not fail.

    Uploads a .pom and a companion artifact to a source repo, then copies
    both to a destination repo via the modify endpoint.  The modify task
    must succeed and the destination version must contain both artifacts plus
    the auto-generated MavenPackage.
    """
    group_id = "com.example.pulp2314"
    artifact_id = "test-lib"
    version = "1.0.0"
    group_path = "com/example/pulp2314/test-lib/1.0.0"

    source_repo = maven_repo_factory()

    # Upload the actual .pom file — this is what triggers _ensure_packages during modify
    pom_path = pom_file_factory(
        group_id=group_id,
        artifact_id=artifact_id,
        version=version,
        name="Test Library",
        packaging="jar",
    )
    pom_content = maven_artifact_api_client.upload(
        file=str(pom_path),
        relative_path=f"{group_path}/{artifact_id}-{version}.pom",
    )

    # Upload a companion .jar artifact for the same GAV
    with tempfile.NamedTemporaryFile(suffix=".jar", delete=False) as f:
        f.write(b"PK\x03\x04" + b"\x00" * 26)
        jar_tmp = f.name
    try:
        jar_content = maven_artifact_api_client.upload(
            file=jar_tmp,
            relative_path=f"{group_path}/{artifact_id}-{version}.jar",
        )
    finally:
        os.unlink(jar_tmp)

    # Add both artifacts to the source repo (first modify — _ensure_packages runs here)
    monitor_task(
        maven_repo_api_client.modify(
            source_repo.pulp_href,
            {"add_content_units": [pom_content.pulp_href, jar_content.pulp_href]},
        ).task
    )

    source_repo = maven_repo_api_client.read(source_repo.pulp_href)
    assert source_repo.latest_version_href.endswith("/versions/1/")

    # Collect the artifact hrefs to copy
    artifacts = maven_artifact_api_client.list(repository_version=source_repo.latest_version_href)
    artifact_hrefs = [a.pulp_href for a in artifacts.results]
    assert len(artifact_hrefs) == 2

    # Copy those artifacts to a destination repo via modify.
    # PULP-2314: _ensure_packages runs again and calls add_content(MavenPackage...)
    # which triggers an AssertionError in the domain check inside add_content.
    dest_repo = maven_repo_factory()
    monitor_task(
        maven_repo_api_client.modify(
            dest_repo.pulp_href,
            {"add_content_units": artifact_hrefs},
        ).task
    )

    dest_repo = maven_repo_api_client.read(dest_repo.pulp_href)
    assert dest_repo.latest_version_href.endswith("/versions/1/")

    dest_artifacts = maven_artifact_api_client.list(
        repository_version=dest_repo.latest_version_href
    )
    assert dest_artifacts.count == 2, "Both artifacts must be in the destination repo"

    dest_packages = maven_package_api_client.list(repository_version=dest_repo.latest_version_href)
    assert dest_packages.count == 1, "MavenPackage must also be in the destination repo"
