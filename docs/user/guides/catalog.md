# Browse the package catalog

Pulp CLI commands for these endpoints are generated from the OpenAPI spec in a separate package; until that is updated, use HTTP.

The content list (`/pulp/api/v3/content/maven/package/`) returns **one row per POM-backed GAV**. For catalog UIs and automation that need **one row per (group_id, artifact_id)**, plus repository metrics, use the repository package index.

Do not use `content/maven/artifact/` for catalog reads: that list is one row per file (jar, pom, checksums).

These endpoints default to the **latest complete repository version**. `{pulp_id}` is the repository UUID. Pass `repository_version` (HREF or PRN) to read a specific version of that repository.

## List packages

```bash
http GET "${BASE_ADDR}/pulp/api/v3/repositories/maven/maven/${REPO_PK}/packages/?limit=10"
```

Pagination `count` is the number of **distinct packages** (`group_id` + `artifact_id`), not GAVs.

Each row includes both a simple version list and per-version metadata:

```json
{
  "group_id": "com.example",
  "artifact_id": "hello",
  "versions": ["1.0.0", "5.3.18"],
  "latest_releases": [
    {
      "version": "1.0.0",
      "release": "",
      "created_at": "2026-08-10T10:45:08.099362Z"
    },
    {
      "version": "5.3.18",
      "release": "rhlw-00003",
      "created_at": "2026-08-11T08:00:00.000000Z"
    }
  ]
}
```

`set(versions)` is always the same as `set(latest_releases[].version)`. There is one `latest_releases` entry per **logical version** (after stripping a trailing rebuild suffix `\.[a-zA-Z]+-\d+$` at the end of `version`).

`5.3.18.rhlw-00003` and `5.3.18.lw-1` share base `5.3.18`. Hyphen qualifiers and other dotted tails are unchanged: `4.3.0-redhat-1`, `5.3.18-anything`, `5.3.18.anything`. `5.3.180` is a different version than `5.3.18`.

`version` is that base. `release` is the stripped suffix without the leading dot (`rhlw-00003`), otherwise empty.

`created_at` is when that logical version entered the repository: `RepositoryContent.pulp_created` of the selected newest rebuild, falling back to the content unit's `pulp_created`.

There is no `…/builds/` endpoint. Clients that want nested `builds[]` load every rebuild of one base version from the content list (see Get one version) and group locally.

### Prefix search

```bash
http GET "${BASE_ADDR}/pulp/api/v3/repositories/maven/maven/${REPO_PK}/packages/" \
  group_id__istartswith==com.example
```

`group_id__istartswith` and `artifact_id__istartswith` are case-insensitive (`ILIKE`). Prefix search belongs on this index, not on the flat content list.

## Repository metrics

```bash
http GET "${BASE_ADDR}/pulp/api/v3/repositories/maven/maven/${REPO_PK}/metrics/"
```

```json
{
  "package_count": 3,
  "version_count": 5,
  "build_count": 6
}
```

Counts use **MavenPackage** units in that repository version (the same identity as PackageList), not MavenArtifact files:

| Field | Identity |
|-------|----------|
| `package_count` | distinct `(group_id, artifact_id)` |
| `version_count` | distinct `(group_id, artifact_id, base_version)` after rebuild-suffix strip |
| `build_count` | distinct `(group_id, artifact_id, full version)` |

Until rebuild suffixes exist, `version_count` equals `build_count`.

## List versions of a package

Use the existing content API. `collapse_builds=true` keeps one unit per logical version (`group_id` + `artifact_id` + `base_version`), the one with the latest `pulp_created`. Do not nest rebuilds on this list. Clients can drain Pulp `next` if the page is full.

```bash
http GET "${BASE_ADDR}/pulp/api/v3/content/maven/package/" \
  group_id==com.example \
  artifact_id==hello \
  collapse_builds==true \
  repository_version=="${LATEST_VERSION_HREF}"
```

Every content row includes `base_version` (stripped version; equal to `version` when there is no rebuild suffix).

## Get one version

Omit `collapse_builds`. Filter with `group_id`, `artifact_id`, and `base_version` so `5.3.18` also hits `5.3.18.rhlw-00003` and does **not** match `5.3.180`. Clients map the flat GAV list to nested `builds[]`.

```bash
http GET "${BASE_ADDR}/pulp/api/v3/content/maven/package/" \
  group_id==com.example \
  artifact_id==hello \
  base_version==5.3.18
```

`version` remains exact. `version__startswith` is case-sensitive; if you use it as a matcher, pass `{version}.` (trailing dot) so `5.3.18` does not match `5.3.180`. Prefer `base_version`.
