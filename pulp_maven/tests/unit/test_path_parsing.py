import pytest

from pulp_maven.app.models import MavenArtifact


@pytest.mark.parametrize(
    "relative_path,expected",
    [
        # (group_id, artifact_id, version, filename)
        (
            "com/example/myapp/1.0.0/myapp-1.0.0.jar",
            ("com.example", "myapp", "1.0.0", "myapp-1.0.0.jar"),
        ),
        # Non-numeric versions must NOT be misparsed as artifactId/version=None.
        (
            "com/example/myapp/RELEASE/myapp-RELEASE.jar",
            ("com.example", "myapp", "RELEASE", "myapp-RELEASE.jar"),
        ),
        (
            "com/example/myapp/master-SNAPSHOT/myapp-master-SNAPSHOT.jar",
            ("com.example", "myapp", "master-SNAPSHOT", "myapp-master-SNAPSHOT.jar"),
        ),
        (
            "com/vsware/svc/INT-7144.1.0.2/svc-INT-7144.1.0.2.jar",
            ("com.vsware.svc", "svc", "INT-7144.1.0.2", "svc-INT-7144.1.0.2.jar"),
        ),
    ],
)
def test_binary_artifact_version_present(relative_path, expected):
    assert (
        MavenArtifact.group_artifact_version_filename(relative_path, version_present=True)
        == expected
    )


def test_artifact_level_metadata_has_no_version():
    # Legacy best-effort path (version_present=None): artifact-level
    # maven-metadata.xml sits directly under the artifactId, no version dir.
    g, a, v, f = MavenArtifact.group_artifact_version_filename(
        "com/example/myapp/maven-metadata.xml"
    )
    assert (g, a, v, f) == ("com.example", "myapp", None, "maven-metadata.xml")
