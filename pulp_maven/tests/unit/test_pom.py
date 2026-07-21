import io

from pulp_maven.app.pom import parse_pom_metadata

FULL_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>my-app</artifactId>
  <version>2.1.0</version>
  <packaging>war</packaging>
  <name>My Application</name>
  <description>A sample application</description>

  <properties>
    <project.url>https://example.com/my-app</project.url>
  </properties>

  <url>${project.url}</url>

  <licenses>
    <license>
      <name>Apache-2.0</name>
      <url>https://www.apache.org/licenses/LICENSE-2.0</url>
    </license>
    <license>
      <name>MIT</name>
      <url>https://opensource.org/licenses/MIT</url>
    </license>
  </licenses>

  <dependencies>
    <dependency>
      <groupId>org.slf4j</groupId>
      <artifactId>slf4j-api</artifactId>
      <version>2.0.0</version>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <version>4.13</version>
      <scope>test</scope>
      <optional>true</optional>
    </dependency>
  </dependencies>

  <scm>
    <url>https://github.com/example/my-app</url>
  </scm>
</project>
"""

PROPERTY_REFS_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>my-app</artifactId>
  <version>3.0.0</version>
  <name>${project.groupId}:${project.artifactId}</name>
  <url>https://github.com/${github.org}/${project.artifactId}</url>

  <properties>
    <github.org>example-corp</github.org>
    <slf4j.version>2.0.9</slf4j.version>
    <junit.version>5.10.0</junit.version>
  </properties>

  <licenses>
    <license>
      <name>Apache-2.0</name>
      <url>https://github.com/${github.org}/${project.artifactId}/blob/v${project.version}/LICENSE</url>
    </license>
  </licenses>

  <dependencies>
    <dependency>
      <groupId>org.slf4j</groupId>
      <artifactId>slf4j-api</artifactId>
      <version>${slf4j.version}</version>
    </dependency>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>${junit.version}</version>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <scm>
    <url>https://github.com/${github.org}/${project.artifactId}</url>
  </scm>
</project>
"""

BARE_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <groupId>org.bare</groupId>
  <artifactId>no-namespace</artifactId>
  <version>0.1</version>
  <name>Bare POM</name>
</project>
"""

MINIMAL_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.minimal</groupId>
  <artifactId>bare-bones</artifactId>
  <version>1.0</version>
</project>
"""


def _parse(xml_str):
    return parse_pom_metadata(io.BytesIO(xml_str.encode()))


def test_full_pom_with_namespace_and_properties():
    result = _parse(FULL_POM)

    assert result["name"] == "My Application"
    assert result["description"] == "A sample application"
    assert result["packaging"] == "war"
    assert result["url"] == "https://example.com/my-app"

    assert len(result["licenses"]) == 2
    assert result["licenses"][0]["name"] == "Apache-2.0"
    assert result["licenses"][1]["name"] == "MIT"

    assert len(result["dependencies"]) == 2
    slf4j = result["dependencies"][0]
    assert slf4j["group_id"] == "org.slf4j"
    assert slf4j["artifact_id"] == "slf4j-api"
    assert slf4j["version"] == "2.0.0"
    assert slf4j["scope"] is None
    assert slf4j["optional"] is False

    junit = result["dependencies"][1]
    assert junit["scope"] == "test"
    assert junit["optional"] is True

    assert result["scm_url"] == "https://github.com/example/my-app"


def test_bare_pom_without_namespace():
    result = _parse(BARE_POM)

    assert result["name"] == "Bare POM"
    assert result["packaging"] == "jar"
    assert result["licenses"] is None
    assert result["dependencies"] is None
    assert result["scm_url"] is None


def test_minimal_pom_nullable_fields():
    result = _parse(MINIMAL_POM)

    assert result["name"] is None
    assert result["description"] is None
    assert result["packaging"] == "jar"
    assert result["url"] is None
    assert result["licenses"] is None
    assert result["dependencies"] is None
    assert result["scm_url"] is None


def test_property_references_resolved():
    result = _parse(PROPERTY_REFS_POM)

    assert result["name"] == "com.example:my-app"
    assert result["url"] == "https://github.com/example-corp/my-app"
    assert result["scm_url"] == "https://github.com/example-corp/my-app"

    assert result["licenses"][0]["url"] == (
        "https://github.com/example-corp/my-app/blob/v3.0.0/LICENSE"
    )

    assert result["dependencies"][0]["version"] == "2.0.9"
    assert result["dependencies"][1]["version"] == "5.10.0"


def test_malformed_xml_returns_none():
    assert _parse("not xml at all") is None
    assert _parse("<project><unclosed>") is None
