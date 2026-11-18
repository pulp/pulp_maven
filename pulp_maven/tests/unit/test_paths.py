import pytest

from pulp_maven.app.models import MavenContentMixin

parse = MavenContentMixin.group_artifact_version_filename


@pytest.mark.parametrize(
    "relative_path, expected",
    [
        (
            "org/springframework/data/spring-data-releasetrain/Neumann-RELEASE/"
            "spring-data-releasetrain-Neumann-RELEASE.pom",
            ("org.springframework.data", "spring-data-releasetrain", "Neumann-RELEASE",
             "spring-data-releasetrain-Neumann-RELEASE.pom"),
        ),
        (
            "org/springframework/session/spring-session-bom/Dragonfruit-RELEASE/"
            "spring-session-bom-Dragonfruit-RELEASE.pom",
            ("org.springframework.session", "spring-session-bom", "Dragonfruit-RELEASE",
             "spring-session-bom-Dragonfruit-RELEASE.pom"),
        ),
        (
            "com/example/lib/1.2.3/lib-1.2.3-sources.jar",
            ("com.example", "lib", "1.2.3", "lib-1.2.3-sources.jar"),
        ),
        (
            "com/example/lib/1.0-SNAPSHOT/lib-1.0-20260101.120000-1.jar",
            ("com.example", "lib", "1.0-SNAPSHOT", "lib-1.0-20260101.120000-1.jar"),
        ),
        (
            "com/example/lib/maven-metadata.xml",
            ("com.example", "lib", None, "maven-metadata.xml"),
        ),
    ],
)
def test_coordinates_from_path(relative_path, expected):
    assert parse(relative_path) == expected
