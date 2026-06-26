# Promote Maven Content Between Repositories

Pulp Maven supports promoting (copying) content between repositories using the `modify` endpoint.
This enables workflows where content is validated in a staging repository before being promoted to
a customer-facing repository — without re-uploading or re-downloading any files.

The `modify` endpoint manipulates content unit references only. No files are copied on disk.

## Prerequisites

You need at least two Maven repositories:

- A **source** repository containing the content you want to promote
- A **destination** repository where content will be promoted to

The examples below use `pulp-cli-maven` and `curl`. Adjust the host, domain, and repository names
to match your environment.

## 1. Set up repositories

Create a source (landing) repository and a destination (validated) repository:

=== "run"

    ```bash
    pulp maven repository create --name landing
    pulp maven repository create --name validated
    ```

## 2. List content in the source repository

Find the content units you want to promote by listing content in the source repository version:

=== "run"

    ```bash
    # Get the latest version href
    REPO_VERSION=$(pulp maven repository show --name landing | jq -r '.latest_version_href')

    # List Maven artifacts in that version
    curl -s -u "$USER:$PASS" -G \
        "https://<pulp-host>/api/pulp/<domain>/api/v3/content/maven/artifact/" \
        --data-urlencode "repository_version=$REPO_VERSION" | jq .
    ```

=== "output"

    ```json
    {
      "count": 3,
      "results": [
        {
          "pulp_href": "/api/pulp/<domain>/api/v3/content/maven/artifact/a1b2c3d4-.../",
          "group_id": "org.example",
          "artifact_id": "my-library",
          "version": "1.0.0",
          "filename": "my-library-1.0.0.jar"
        }
      ]
    }
    ```

## 3. Promote specific content units

Copy one or more content units from the source to the destination repository using the `modify`
endpoint:

=== "run"

    ```bash
    DEST_HREF=$(pulp maven repository show --name validated | jq -r '.pulp_href')

    curl -s -u "$USER:$PASS" -X POST \
        -H "Content-Type: application/json" \
        -d '{"add_content_units": ["<content-unit-href-1>", "<content-unit-href-2>"]}' \
        "https://<pulp-host>${DEST_HREF}modify/" | jq .
    ```

=== "output"

    ```json
    {
      "task": "/api/pulp/<domain>/api/v3/tasks/b1c2d3e4-.../"
    }
    ```

This creates a new repository version on the destination containing the specified content units.

## 4. Promote all content using base_version

To copy the entire contents of a specific repository version (e.g., to create an exact replica),
use `base_version`:

=== "run"

    ```bash
    SOURCE_VERSION=$(pulp maven repository show --name landing | jq -r '.latest_version_href')
    DEST_HREF=$(pulp maven repository show --name validated | jq -r '.pulp_href')

    curl -s -u "$USER:$PASS" -X POST \
        -H "Content-Type: application/json" \
        -d "{\"base_version\": \"$SOURCE_VERSION\"}" \
        "https://<pulp-host>${DEST_HREF}modify/" | jq .
    ```

This sets the destination repository's content to exactly match the source version.

## 5. Remove content from a repository

Remove specific content units from a repository:

=== "run"

    ```bash
    REPO_HREF=$(pulp maven repository show --name validated | jq -r '.pulp_href')

    curl -s -u "$USER:$PASS" -X POST \
        -H "Content-Type: application/json" \
        -d '{"remove_content_units": ["<content-unit-href>"]}' \
        "https://<pulp-host>${REPO_HREF}modify/" | jq .
    ```

To remove **all** content from a repository (creating an empty version):

=== "run"

    ```bash
    curl -s -u "$USER:$PASS" -X POST \
        -H "Content-Type: application/json" \
        -d '{"remove_content_units": ["*"]}' \
        "https://<pulp-host>${REPO_HREF}modify/" | jq .
    ```

## Workflow Example: Staging to Production

A typical promotion pipeline:

```
landing repo  ──(validate)──>  validated repo  ──(approve)──>  production repo
```

```bash
# 1. Content arrives in landing (via deploy, sync, or cache)

# 2. After validation, promote to validated repo
VALIDATED_HREF=$(pulp maven repository show --name validated | jq -r '.pulp_href')
curl -s -u "$USER:$PASS" -X POST \
    -H "Content-Type: application/json" \
    -d '{"add_content_units": ["<content-unit-href>"]}' \
    "https://<pulp-host>${VALIDATED_HREF}modify/"

# 3. After approval, promote to production
PRODUCTION_HREF=$(pulp maven repository show --name production | jq -r '.pulp_href')
curl -s -u "$USER:$PASS" -X POST \
    -H "Content-Type: application/json" \
    -d '{"add_content_units": ["<content-unit-href>"]}' \
    "https://<pulp-host>${PRODUCTION_HREF}modify/"
```

Each repository can have its own distribution, giving consumers access to content at the
appropriate stage of the pipeline.

## API Reference

`POST /pulp/api/v3/repositories/maven/maven/{uuid}/modify/`

On multi-domain deployments the path includes the domain prefix (e.g.
`/api/pulp/<domain>/api/v3/repositories/maven/maven/{uuid}/modify/`).

| Field | Type | Description |
|-------|------|-------------|
| `add_content_units` | list of hrefs | Content unit HREFs to add to the repository |
| `remove_content_units` | list of hrefs | Content unit HREFs to remove (`["*"]` removes all) |
| `base_version` | href | A repository version HREF; sets content to match that version |

The endpoint returns a task that creates a new repository version with the requested changes.
