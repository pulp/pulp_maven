# Publications and Autopublish

A MavenPublication generates `maven-metadata.xml` files for every artifact in a repository version.
These metadata files list all available versions for each `(group_id, artifact_id)` pair, enabling
Maven clients to discover and resolve artifact versions automatically.

Publications also generate `.md5`, `.sha1`, and `.sha256` checksum files alongside each
`maven-metadata.xml` so that clients can verify metadata integrity.

## How maven-metadata.xml is generated

When a publication is created, Pulp scans all `MavenArtifact` content units in the repository
version and groups them by `(group_id, artifact_id)`. For each group, it generates a
`maven-metadata.xml` at `<group_path>/<artifact_id>/maven-metadata.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <groupId>org.example</groupId>
  <artifactId>my-library</artifactId>
  <versioning>
    <latest>2.0.0</latest>
    <release>2.0.0</release>
    <versions>
      <version>1.0.0</version>
      <version>1.1.0</version>
      <version>2.0.0</version>
    </versions>
    <lastUpdated>20260630120000</lastUpdated>
  </versioning>
</metadata>
```

| Field | Description |
|-------|-------------|
| `latest` | The highest version (sorted lexicographically) |
| `release` | The highest non-SNAPSHOT version |
| `versions` | All versions in sorted order |
| `lastUpdated` | UTC timestamp when the publication was created |

The publication uses `pass_through=True`, which means original artifacts (JARs, POMs, etc.) are
served directly from storage while the generated metadata is served from the publication.

## Prerequisites

Create a Maven repository and distribution.

The examples below use `pulp-cli-maven` and `curl`. Adjust the host, domain, and repository names
to match your environment.

=== "run"

    ```bash
    pulp maven repository create --name maven-releases
    pulp maven distribution create \
        --name maven-releases \
        --repository maven-releases \
        --base-path maven-releases
    ```

Upload or sync some content into the repository so there are artifacts to publish.

## Creating a publication manually

Trigger a publication by providing either a `repository` (uses the latest version) or a specific
`repository_version`. The publication API is at `/pulp/api/v3/publications/maven/maven/`.

!!! note
    The `pulp` CLI does not yet have a `maven publication` subcommand. Use curl to create
    publications.

=== "run"

    ```bash
    # Publish the latest repository version
    REPO_HREF=$(pulp maven repository show --name maven-releases | jq -r '.pulp_href')

    curl -s -u "$USER:$PASS" -X POST \
        -H "Content-Type: application/json" \
        -d "{\"repository\": \"$REPO_HREF\"}" \
        "https://<pulp-host>/pulp/<domain>/api/v3/publications/maven/maven/"
    ```

=== "output"

    ```json
    {
      "task": "/pulp/api/v3/tasks/a1b2c3d4-.../"
    }
    ```

To publish a specific repository version instead:

```bash
REPO_VERSION=$(pulp maven repository show --name maven-releases | jq -r '.latest_version_href')

curl -s -u "$USER:$PASS" -X POST \
    -H "Content-Type: application/json" \
    -d "{\"repository_version\": \"$REPO_VERSION\"}" \
    "https://<pulp-host>/pulp/<domain>/api/v3/publications/maven/maven/"
```

After the task completes, a `MavenPublication` exists with a generated `maven-metadata.xml`
(and its `.md5`, `.sha1`, `.sha256` checksums) for each `(group_id, artifact_id)` pair in
the repository version. Artifact files themselves (JARs, POMs, etc.) are served directly
from storage without any additional generated files.

### Pointing a distribution to a publication

To serve a specific publication snapshot, update your distribution to reference the publication
instead of the repository:

```bash
DIST_HREF=$(pulp maven distribution show --name maven-releases | jq -r '.pulp_href')

PUB_HREF=$(curl -s -u "$USER:$PASS" \
    "https://<pulp-host>/pulp/<domain>/api/v3/publications/maven/maven/?limit=1&ordering=-pulp_created" \
    | jq -r '.results[0].pulp_href')

curl -s -u "$USER:$PASS" -X PATCH \
    -H "Content-Type: application/json" \
    -d "{\"publication\": \"$PUB_HREF\", \"repository\": null}" \
    "https://<pulp-host>${DIST_HREF}"
```

When a distribution points to a publication, consumers see the exact content from that repository
version plus the generated metadata files. When it points to a repository, it always serves the
latest version but without generated metadata.

## Autopublish

Autopublish automatically creates a new publication every time a repository version is created.
This removes the need to manually trigger publications after each content change.

### Enabling autopublish

Set `autopublish` to `true` when creating or updating a repository:

!!! note
    The `pulp` CLI does not yet expose the `autopublish` flag for Maven repositories.

=== "run"

    ```bash
    curl -s -u "$USER:$PASS" -X POST \
        -H "Content-Type: application/json" \
        -d '{"name": "maven-releases", "autopublish": true}' \
        "https://<pulp-host>/pulp/<domain>/api/v3/repositories/maven/maven/"

    # On an existing repository
    REPO_HREF=$(pulp maven repository show --name maven-releases | jq -r '.pulp_href')

    curl -s -u "$USER:$PASS" -X PATCH \
        -H "Content-Type: application/json" \
        -d '{"autopublish": true}' \
        "https://<pulp-host>${REPO_HREF}"
    ```

=== "output"

    ```json
    {
      "pulp_href": "/pulp/api/v3/repositories/maven/maven/a1b2c3d4-.../",
      "name": "maven-releases",
      "autopublish": true,
      "..."
    }
    ```

### How autopublish works

When `autopublish` is enabled:

1. Any operation that creates a new repository version (upload, sync, modify, deploy API) triggers
   the `on_new_version` hook
2. The hook calls the `publish` task, which generates `maven-metadata.xml` and checksums for the
   new version
3. If the distribution points to the repository, it automatically serves the new publication

This means every content change immediately produces up-to-date `maven-metadata.xml` files
without manual intervention.

### Disabling autopublish

```bash
REPO_HREF=$(pulp maven repository show --name maven-releases | jq -r '.pulp_href')

curl -s -u "$USER:$PASS" -X PATCH \
    -H "Content-Type: application/json" \
    -d '{"autopublish": false}' \
    "https://<pulp-host>${REPO_HREF}"
```

Existing publications are not deleted when autopublish is disabled.

## Consuming maven-metadata.xml for version discovery

Once a repository has been published (either manually or via autopublish), Maven clients can
discover available versions through the generated metadata.

### Configuring Maven to use the repository

Add Pulp as a repository in your `~/.m2/settings.xml` or `pom.xml`:

```xml title="~/.m2/settings.xml"
<settings>
  <profiles>
    <profile>
      <id>pulp</id>
      <repositories>
        <repository>
          <id>pulp-releases</id>
          <url>https://pulp.local/pulp/my-domain/content/maven-releases/</url>
        </repository>
      </repositories>
    </profile>
  </profiles>
  <activeProfiles>
    <activeProfile>pulp</activeProfile>
  </activeProfiles>
</settings>
```

### How Maven uses the metadata

When Maven resolves a dependency, it fetches `maven-metadata.xml` to find available versions:

```
GET /pulp/<domain>/content/maven-releases/org/example/my-library/maven-metadata.xml
```

Maven uses this metadata to:

- **Resolve version ranges** — a dependency on `[1.0,2.0)` uses the versions list to find the
  best match
- **Resolve `LATEST` and `RELEASE`** — these markers map to the `<latest>` and `<release>`
  fields in the metadata
- **Check for updates** — `mvn versions:display-dependency-updates` reads the metadata to report
  newer versions

### Verifying metadata with checksums

Clients can verify metadata integrity by fetching the companion checksum files:

```
GET /pulp/<domain>/content/maven-releases/org/example/my-library/maven-metadata.xml.sha256
GET /pulp/<domain>/content/maven-releases/org/example/my-library/maven-metadata.xml.sha1
GET /pulp/<domain>/content/maven-releases/org/example/my-library/maven-metadata.xml.md5
```

Maven checks these automatically when downloading metadata.

### Fetching metadata directly

You can also inspect the metadata outside of Maven using curl:

```bash
curl -s "https://<pulp-host>/pulp/<domain>/content/maven-releases/org/example/my-library/maven-metadata.xml"
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <groupId>org.example</groupId>
  <artifactId>my-library</artifactId>
  <versioning>
    <latest>2.0.0</latest>
    <release>2.0.0</release>
    <versions>
      <version>1.0.0</version>
      <version>1.1.0</version>
      <version>2.0.0</version>
    </versions>
    <lastUpdated>20260630120000</lastUpdated>
  </versioning>
</metadata>
```

## Recommended workflow

For pipeline teams that want consumers to always have access to up-to-date version metadata:

1. **Create the repository with autopublish enabled**

    ```bash
    curl -s -u "$USER:$PASS" -X POST \
        -H "Content-Type: application/json" \
        -d '{"name": "maven-releases", "autopublish": true}' \
        "https://<pulp-host>/pulp/<domain>/api/v3/repositories/maven/maven/"
    ```

2. **Create a distribution pointing to the repository**

    ```bash
    pulp maven distribution create \
        --name maven-releases \
        --repository maven-releases \
        --base-path maven-releases
    ```

3. **Upload or deploy content** using any method (upload API, deploy API, sync, promote)

4. **Consumers resolve artifacts** — Maven clients pointed at the distribution URL automatically
   discover versions via the generated `maven-metadata.xml`

No manual publication step is needed. Each content change creates a new repository version, which
triggers autopublish, which generates fresh metadata.
