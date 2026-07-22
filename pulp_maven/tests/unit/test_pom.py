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

PARENT_AND_DEP_MGMT_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.0</version>
    <relativePath/>
  </parent>

  <groupId>com.example</groupId>
  <artifactId>my-service</artifactId>
  <version>1.0.0</version>
  <name>My Service</name>

  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>org.managed</groupId>
        <artifactId>managed-lib</artifactId>
        <version>9.9.9</version>
      </dependency>
    </dependencies>
  </dependencyManagement>

  <dependencies>
    <dependency>
      <groupId>org.managed</groupId>
      <artifactId>managed-lib</artifactId>
    </dependency>
    <dependency>
      <groupId>org.direct</groupId>
      <artifactId>direct-lib</artifactId>
      <version>2.0</version>
    </dependency>
  </dependencies>
</project>
"""

EMPTY_SECTIONS_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>empty-sections</artifactId>
  <version>1.0</version>
  <licenses></licenses>
  <dependencies></dependencies>
</project>
"""

UNRESOLVABLE_PROPS_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>unresolvable</artifactId>
  <version>1.0</version>
  <name>${nonexistent.prop}</name>
  <url>https://${missing.host}/path</url>
</project>
"""

CHAINED_PROPS_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>chained</artifactId>
  <version>1.0</version>

  <properties>
    <base.host>example.com</base.host>
    <site.url>https://${base.host}</site.url>
  </properties>

  <url>${site.url}/docs</url>
</project>
"""

CIRCULAR_PROPS_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>circular</artifactId>
  <version>1.0</version>

  <properties>
    <prop.a>${prop.b}</prop.a>
    <prop.b>${prop.a}</prop.b>
  </properties>

  <name>${prop.a}</name>
</project>
"""

OPTIONAL_VARIANTS_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>optional-variants</artifactId>
  <version>1.0</version>

  <dependencies>
    <dependency>
      <groupId>a</groupId>
      <artifactId>explicit-true</artifactId>
      <version>1.0</version>
      <optional>true</optional>
    </dependency>
    <dependency>
      <groupId>b</groupId>
      <artifactId>explicit-false</artifactId>
      <version>1.0</version>
      <optional>false</optional>
    </dependency>
    <dependency>
      <groupId>c</groupId>
      <artifactId>uppercase-true</artifactId>
      <version>1.0</version>
      <optional>TRUE</optional>
    </dependency>
    <dependency>
      <groupId>d</groupId>
      <artifactId>absent-optional</artifactId>
      <version>1.0</version>
    </dependency>
  </dependencies>
</project>
"""

SCM_NO_URL_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>scm-no-url</artifactId>
  <version>1.0</version>

  <scm>
    <connection>scm:git:git://github.com/example/repo.git</connection>
    <developerConnection>scm:git:ssh://github.com/example/repo.git</developerConnection>
    <tag>v1.0</tag>
  </scm>
</project>
"""

BARE_FULL_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <groupId>org.bare</groupId>
  <artifactId>full-bare</artifactId>
  <version>1.0</version>

  <licenses>
    <license>
      <name>MIT</name>
      <url>https://opensource.org/licenses/MIT</url>
    </license>
  </licenses>

  <dependencies>
    <dependency>
      <groupId>org.example</groupId>
      <artifactId>some-lib</artifactId>
      <version>3.0</version>
    </dependency>
  </dependencies>
</project>
"""

PROFILES_AND_MODULES_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>multi-module</artifactId>
  <version>1.0</version>
  <packaging>pom</packaging>

  <modules>
    <module>child-a</module>
    <module>child-b</module>
  </modules>

  <profiles>
    <profile>
      <id>dev</id>
      <dependencies>
        <dependency>
          <groupId>org.profile</groupId>
          <artifactId>profile-only</artifactId>
          <version>1.0</version>
        </dependency>
      </dependencies>
    </profile>
  </profiles>

  <dependencies>
    <dependency>
      <groupId>org.real</groupId>
      <artifactId>real-dep</artifactId>
      <version>2.0</version>
    </dependency>
  </dependencies>
</project>
"""

PARTIAL_LICENSE_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>partial-license</artifactId>
  <version>1.0</version>

  <licenses>
    <license>
      <url>https://www.apache.org/licenses/LICENSE-2.0</url>
    </license>
    <license>
      <name>MIT</name>
    </license>
  </licenses>
</project>
"""

WHITESPACE_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>whitespace</artifactId>
  <version>1.0</version>
  <name>  My App  </name>
  <description>
    A multi-line
    description
  </description>
</project>
"""

COMMENTS_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <!-- This is a project comment -->
  <groupId>com.example</groupId>
  <artifactId>with-comments</artifactId>
  <version>1.0</version>
  <name>Commented Project</name>
  <!-- License section -->
  <licenses>
    <!-- Apache license -->
    <license>
      <name>Apache-2.0</name>
      <url>https://www.apache.org/licenses/LICENSE-2.0</url>
    </license>
  </licenses>
</project>
"""

DEP_EXTRAS_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
  <artifactId>dep-extras</artifactId>
  <version>1.0</version>

  <dependencies>
    <dependency>
      <groupId>org.example</groupId>
      <artifactId>with-extras</artifactId>
      <version>1.0</version>
      <type>war</type>
      <classifier>sources</classifier>
      <exclusions>
        <exclusion>
          <groupId>org.unwanted</groupId>
          <artifactId>unwanted-lib</artifactId>
        </exclusion>
      </exclusions>
    </dependency>
  </dependencies>
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


def test_parent_and_dependency_management():
    result = _parse(PARENT_AND_DEP_MGMT_POM)

    assert result["name"] == "My Service"

    assert len(result["dependencies"]) == 2
    managed = result["dependencies"][0]
    assert managed["group_id"] == "org.managed"
    assert managed["artifact_id"] == "managed-lib"
    assert managed["version"] == ""

    direct = result["dependencies"][1]
    assert direct["group_id"] == "org.direct"
    assert direct["version"] == "2.0"


def test_empty_sections_return_empty_lists():
    result = _parse(EMPTY_SECTIONS_POM)

    assert result["licenses"] == []
    assert result["dependencies"] == []
    assert result["licenses"] is not None
    assert result["dependencies"] is not None


def test_unresolvable_property_references_preserved():
    result = _parse(UNRESOLVABLE_PROPS_POM)

    assert result["name"] == "${nonexistent.prop}"
    assert "${missing.host}" in result["url"]


def test_chained_property_resolution():
    result = _parse(CHAINED_PROPS_POM)

    assert result["url"] == "https://example.com/docs"


def test_circular_property_resolution_terminates():
    result = _parse(CIRCULAR_PROPS_POM)

    assert result["name"] is not None
    assert "${" in result["name"]


def test_optional_flag_variants():
    result = _parse(OPTIONAL_VARIANTS_POM)

    deps = {d["artifact_id"]: d for d in result["dependencies"]}
    assert deps["explicit-true"]["optional"] is True
    assert deps["explicit-false"]["optional"] is False
    assert deps["uppercase-true"]["optional"] is False
    assert deps["absent-optional"]["optional"] is False


def test_scm_without_url():
    result = _parse(SCM_NO_URL_POM)

    assert result["scm_url"] is None


def test_bare_pom_with_licenses_and_dependencies():
    result = _parse(BARE_FULL_POM)

    assert len(result["licenses"]) == 1
    assert result["licenses"][0]["name"] == "MIT"

    assert len(result["dependencies"]) == 1
    assert result["dependencies"][0]["group_id"] == "org.example"
    assert result["dependencies"][0]["version"] == "3.0"


def test_profiles_and_modules_ignored():
    result = _parse(PROFILES_AND_MODULES_POM)

    assert result["packaging"] == "pom"
    assert len(result["dependencies"]) == 1
    assert result["dependencies"][0]["group_id"] == "org.real"


def test_partial_license_fields():
    result = _parse(PARTIAL_LICENSE_POM)

    assert len(result["licenses"]) == 2
    assert result["licenses"][0]["name"] == ""
    assert result["licenses"][0]["url"] == "https://www.apache.org/licenses/LICENSE-2.0"
    assert result["licenses"][1]["name"] == "MIT"
    assert result["licenses"][1]["url"] == ""


def test_whitespace_in_element_text():
    result = _parse(WHITESPACE_POM)

    assert result["name"] == "  My App  "
    assert "\n" in result["description"]


def test_xml_comments_ignored():
    result = _parse(COMMENTS_POM)

    assert result["name"] == "Commented Project"
    assert len(result["licenses"]) == 1
    assert result["licenses"][0]["name"] == "Apache-2.0"


def test_dependency_with_type_classifier_exclusions():
    result = _parse(DEP_EXTRAS_POM)

    assert len(result["dependencies"]) == 1
    dep = result["dependencies"][0]
    assert dep["group_id"] == "org.example"
    assert dep["artifact_id"] == "with-extras"
    assert dep["version"] == "1.0"
    assert dep["scope"] is None
    assert dep["optional"] is False


def test_malformed_xml_returns_none():
    assert _parse("not xml at all") is None
    assert _parse("<project><unclosed>") is None
