# Maven Packages

A single Maven release — for example `spring-cloud-config-server` version
`4.3.0` — is not one file. It is delivered as several files that live side by
side: the main JAR, the **POM** (an XML file that describes the project: its name,
license, dependencies, and so on), often a sources or javadoc JAR, and a set of
checksum files.

Pulp already tracks each of those files on its own as a **`MavenArtifact`**. A
**`MavenPackage`** is one extra record that stands for the *whole release*. It
carries the human-readable details Pulp reads out of the POM, so you can browse and
search your content one release at a time instead of file by file. This is the same
idea as the package-level content types in `pulp_rpm` and `pulp_python`.

A release is identified by three coordinates, together called the **GAV**:

- `group_id` — the organization or project (e.g. `org.springframework.cloud`)
- `artifact_id` — the specific library (e.g. `spring-cloud-config-server`)
- `version` — the release version (e.g. `4.3.0`)

A `MavenPackage` has no file of its own; it simply groups the artifacts that share
the same GAV.

## How packages are created

You never create a package by hand — Pulp does it for you. Because all of a
release's details come from its POM, a package is created automatically as soon as
Pulp has that release's `.pom` file. The most common way that happens is when you:

- **Upload** a `.pom` through the content API (`content/maven/artifact/`). See
  [Upload Maven Artifacts](upload.md).
- **Deploy** a project with `mvn deploy`, which uploads the POM through the
  [Maven Deploy Plugin](deploy.md).

Once the package exists, Pulp keeps it in step with the files in each repository:
the package appears in a repository as long as that release's files are present,
and it drops out once all of the release's files have been removed. You don't have
to add or remove packages yourself.

If a release's files are uploaded *without* its `.pom`, no package is created for
it until the POM shows up.

Content added through [pull-through caching](create-cache.md) is one exception: its
packages appear only once the cached files are saved into a repository (the "add
cached content" step), not while they are being streamed to clients.

## What Pulp reads from the POM

When a package is created (or refreshed — see below), Pulp reads the POM and stores
these details:

| Field | Description |
|-------|-------------|
| `group_id` | Group Id of the release. |
| `artifact_id` | Artifact Id of the release. |
| `version` | Version of the release. |
| `name` | Human-readable project name. |
| `description` | Project description. |
| `packaging` | Packaging type (`jar`, `war`, `pom`, …). Assumed to be `jar` if the POM doesn't say. |
| `url` | Project URL. |
| `licenses` | The project's licenses (each with a `name` and `url`). |
| `dependencies` | The project's dependencies (`group_id`, `artifact_id`, `version`, `scope`, `optional`). |
| `scm_url` | Source-control URL. |

POMs often use placeholders such as `${project.version}` instead of literal values.
Pulp fills these in from the POM's own properties before storing the details.

## Releases versus snapshots

A **release** package is fixed: once Pulp has read its POM, the stored details do
not change.

A **`-SNAPSHOT`** package is a work in progress, so Pulp refreshes its details every
time a newer POM for that version is uploaded. A snapshot package therefore always
reflects the most recent POM.

## Browsing packages via the REST API

The package API is **read-only**. You can list and search packages, but you cannot
create, change, or delete them through it — Pulp manages them automatically, as
described above.

```
GET /pulp/api/v3/content/maven/package/
```

The list is paginated and can be filtered by `group_id`, `artifact_id`, `version`,
`name`, and `packaging`. `version` also supports case-sensitive `version__startswith`.
`base_version` matches a logical version after stripping a trailing rebuild suffix
`\.[a-zA-Z]+-\d+$` (`5.3.18` matches `5.3.18` and `5.3.18.rhlw-00003`, but not
`5.3.180` or `5.3.18-anything`).
`collapse_builds=true` keeps one row per logical version. Filtering by the GAV
coordinates (`group_id`, `artifact_id`, `version`) is the fastest way to find a
specific release.

For a **package catalog** (one row per groupId/artifactId, search, prefix filters, and
repository metrics), see [Browse the package catalog](catalog.md).

=== "curl"

    ```bash
    curl -s -u admin:password \
      "https://pulp-hostname/pulp/api/v3/content/maven/package/?group_id=org.springframework.cloud&artifact_id=spring-cloud-config-server" \
      | jq .
    ```

    ```json
    {
      "count": 1,
      "next": null,
      "previous": null,
      "results": [
        {
          "pulp_href": "/pulp/api/v3/content/maven/package/<uuid>/",
          "pulp_created": "2026-08-17T12:00:00.000000Z",
          "group_id": "org.springframework.cloud",
          "artifact_id": "spring-cloud-config-server",
          "version": "4.3.0-redhat-1",
          "base_version": "4.3.0-redhat-1",
          "name": "Spring Cloud Config Server",
          "description": "Spring Cloud Config Server",
          "packaging": "jar",
          "url": "https://spring.io/projects/spring-cloud-config",
          "licenses": [
            {"name": "Apache License, Version 2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"}
          ],
          "dependencies": [
            {"group_id": "org.springframework.boot", "artifact_id": "spring-boot-starter-web", "version": "3.3.0", "scope": null, "optional": false}
          ],
          "scm_url": "https://github.com/spring-cloud/spring-cloud-config"
        }
      ]
    }
    ```

=== "python"

    ```python
    from pulpcore.client.pulp_maven import ApiClient, ContentPackageApi, Configuration

    configuration = Configuration(
        host="https://pulp-hostname",
        username="admin",
        password="password",
    )
    client = ApiClient(configuration)
    api = ContentPackageApi(client)

    packages = api.list(
        group_id="org.springframework.cloud",
        artifact_id="spring-cloud-config-server",
    )
    for pkg in packages.results:
        print(pkg.group_id, pkg.artifact_id, pkg.version, pkg.packaging)
    ```

The auto-generated [REST API reference](https://pulpproject.org/pulp_maven/restapi/)
is the authoritative source for the full response schema and available filters.

## Upgrading an existing Pulp

The first time you upgrade to a release that includes Maven packages, Pulp looks at
the POM files you already have and creates a package for each release it finds. On a
Pulp instance with a very large number of artifacts, this one-time step can take a
while.

At that point the packages exist but are not yet attached to your existing
repositories. Each package joins a repository the next time that repository's
content changes — for example on your next upload, deploy, promote, or "add cached
content" operation.
