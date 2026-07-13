# Maven Metadata Management

Pulp Maven automatically generates and maintains `maven-metadata.xml` files and their checksums
(`md5`, `sha1`, `sha256`) for every artifact in a repository. No manual publish step is required.

## How metadata is generated

Whenever a new repository version is created — whether by uploading artifacts, promoting content
between repositories, deploying via the Maven Deploy Plugin, or adding cached content — Pulp
automatically generates the appropriate `maven-metadata.xml` files as part of the version
finalization process.

This means:

- **No publication step is needed.** Distributions point directly at repositories, and metadata
  is always up to date with the latest repository version.
- **Metadata is incremental.** Only the `(group_id, artifact_id)` pairs that were added or
  removed in a given version are regenerated — not the entire repository.
- **SNAPSHOT metadata is handled automatically.** For versions ending in `-SNAPSHOT`, Pulp
  generates version-level `maven-metadata.xml` files containing the list of snapshot artifacts.

### What gets generated

For each affected `(group_id, artifact_id)` pair, Pulp creates:

| File | Path | Contents |
|------|------|----------|
| `maven-metadata.xml` | `<group_path>/<artifact_id>/` | Lists all versions of the artifact |
| `maven-metadata.xml.md5` | `<group_path>/<artifact_id>/` | MD5 checksum |
| `maven-metadata.xml.sha1` | `<group_path>/<artifact_id>/` | SHA-1 checksum |
| `maven-metadata.xml.sha256` | `<group_path>/<artifact_id>/` | SHA-256 checksum |

For SNAPSHOT versions, the same set of files is also generated under
`<group_path>/<artifact_id>/<version>/`.

## Simplified workflow

The typical workflow for serving Maven content through Pulp is:

```
1. Create a repository
2. Create a distribution pointing at the repository
3. Add content (upload, deploy, cache, or promote)
4. Content is served — metadata is already up to date
```

=== "curl"

    ```bash
    # 1. Create repository
    curl -s -u user:password -X POST \
        https://pulp-hostname/pulp/my-domain/api/v3/repositories/maven/maven/ \
        -H "Content-Type: application/json" \
        -d '{"name": "my-repo"}' | jq .

    # 2. Create distribution
    REPO_HREF=$(curl -s -u user:password \
        https://pulp-hostname/pulp/my-domain/api/v3/repositories/maven/maven/ \
        | jq -r '.results[] | select(.name=="my-repo") | .pulp_href')

    curl -s -u user:password -X POST \
        https://pulp-hostname/pulp/my-domain/api/v3/distributions/maven/maven/ \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"my-repo\", \"base_path\": \"my-repo\", \"repository\": \"$REPO_HREF\"}" | jq .

    # 3. Upload content (metadata is generated automatically)
    curl -s -u user:password -X POST \
        https://pulp-hostname/pulp/my-domain/api/v3/content/maven/artifact/ \
        -F "file=@my-library-1.0.0.jar" \
        -F "relative_path=org/example/my-library/1.0.0/my-library-1.0.0.jar" \
        -F "repository=$REPO_HREF"

    # 4. Content and metadata are served at:
    #    https://pulp-hostname/pulp/content/my-domain/my-repo/
    ```

## Repair metadata

If metadata becomes inconsistent (e.g., after a migration or manual database change),
you can regenerate all `maven-metadata.xml` files for a repository using the `repair_metadata`
endpoint.

This dispatches an asynchronous task that rebuilds metadata for every
`(group_id, artifact_id)` pair in the latest repository version.

=== "curl"

    ```bash
    REPO_HREF=$(curl -s -u user:password \
        https://pulp-hostname/pulp/my-domain/api/v3/repositories/maven/maven/ \
        | jq -r '.results[] | select(.name=="my-repo") | .pulp_href')

    curl -s -u user:password -X POST \
        "https://pulp-hostname${REPO_HREF}repair_metadata/" | jq .
    ```

    ```json
    {
      "task": "/pulp/default/api/v3/tasks/<uuid>/"
    }
    ```

## Pull-through caching and metadata

When using [pull-through caching](create-cache.md), metadata generation is deferred. Content
streamed from a remote is saved into the repository without regenerating metadata on every
request — this avoids blocking the content app. Metadata is generated when content is explicitly
added to a repository version through other operations (upload, promote, or the
`add_cached_content` action).

## API reference

`POST /pulp/default/api/v3/repositories/maven/maven/{uuid}/repair_metadata/`

Triggers a full metadata regeneration for the repository. Returns a 202 response with a task href.
