# Experimental Maven path-index integration

See the [glossary](path-index-glossary.md), [engine](path-index-experiment.md), and
[S3 backend](path-index-s3.md). This integration is disabled by default. It requires
`pulp_labels["path_index"] = "true"` and global mode `shadow` or `serve`.

The supported upload workflow creates artifacts/content as orphans, then adds them
with the repository `modify` endpoint. Index publication happens in that task's
finalization, under its existing repository reservation. There is no five-second
completion target or special handling for `maven_deploy`.

This PR leaves HTML generation, hosted authentication, content guards, and CDN
configuration unchanged. The independent HTML optimization uses the repository
label `incremental_index_pages=true`; neither feature requires the other. No
pulpcore changes or new `/auth` endpoint are included.

## Publication and failure policy

1. Maven finishes package, metadata, HTML page, and Bloom-filter work.
2. The finalizer reads the previous completed version's manifest descriptor. With
   no descriptor it streams an initial build; otherwise it reads only changed paths
   to construct a delta, including replacements, deletions, and directory aliases.
3. It uploads new segments, verifies predecessor objects, then uploads the immutable
   version manifest. Normal updates do not rewrite the base. Equal-level deltas are
   compacted as needed; reaching the segment cap can require a full base rewrite.
4. It attaches a descriptor to `RepositoryVersion.info["maven_path_index"]`. This
   contains a descriptor format number, storage profile, manifest SHA-256, and a
   checkpoint flag, roughly 200 bytes. No journal or additional index-state tables
   are created.
5. Pulpcore saves that info together with `complete=True`, then performs retention.
   Consumers select completed versions only. The version UUID and descriptor digest
   identify exactly which immutable S3 manifest to load.

**Failure to publish fails the modify task.** The previous completed version remains
available, and uploaded orphan content remains available for retry until normal
orphan cleanup removes it. A failure after S3 upload but before DB completion leaves
unreferenced index objects; it cannot advertise the failed version to readers.
There is no cross-service transaction or asynchronous publication queue. No-op
versions do not publish anything.

Retention can delete the previous version and squash membership history after the
new manifest is safely published. Every manifest describes its full base/delta set;
opening it does not require the previous version's DB row. Retention must not trigger
blind deletion of shared S3 segments. Automatic index-object collection is not yet
implemented.

## Consumer refresh and reads

The content worker caches a small serving descriptor per distribution for
`REFRESH_SECONDS`. Refresh queries select version IDs and `info`, never the large
`content_ids` membership array. A token containing repository UUID, version UUID,
storage profile, and manifest digest keys the prepared view cache. Backfill or
compaction of the same version changes the token, so readers can adopt it on refresh.
There are no S3 HEAD requests or new Redis operations on each artifact lookup.

A bounded background executor downloads a selected manifest and verifies its digest
against the committed descriptor. It then opens the required segments. Workers in
one pod share a local directory, download locks, and mmap file pages. Other pods
have their own caches. An old version's index is never substituted for a new
version's view. There are no journal overlays.

Only a complete view can produce an indexed 404. Missing, invalid, cold, or over-budget
views use the existing resolver while preparation is retried. Descriptor refresh
adds up to `REFRESH_SECONDS` of staleness when following the latest version; pinned
distributions remain pinned. This is separate from core's distribution-cache TTL.
Repository/descriptor refresh still uses bounded SQL, and existing guard checks
still run. Only warm, eligible requests avoid per-artifact membership queries.

The writer requires downloaded artifacts with the supported digest storage layout
and complete generated directory pages. An unresolved on-demand artifact or missing
page fails an opted-in update instead of publishing incomplete coverage. Do not opt
in repositories that depend on unresolved content. Remote/pull-through distributions,
checkpoint distributions, redirect guards, and unsupported artifact backends use
the existing request flow.

## Rollout and maintenance

1. Install `pulp-maven[path-index-s3]`. Configure task and content processes
   consistently, including bucket/prefix and shared local cache paths within each
   pod. Use the normal boto3 credential chain. GetObject and PutObject, including
   conditional writes, are required; ListBucket and DeleteObject are not.
2. Select `MAVEN_PATH_INDEX_MODE = "shadow"` and label a test repository. Shadow mode
   publishes during finalization and warms requested views, but responses still use
   the existing resolver. S3 failure fails writes in both shadow and serve modes.
   Shadow mode does not automatically compare index and database answers.
3. POST `<repository_href>build_path_index/` for an existing repository. This task
   reserves the repository, builds the latest completed version, then records its
   descriptor. Modify tasks queue during the build. Existing content keeps serving
   through the DB. No new repository version or HTML is generated. The first normal
   modification also builds a baseline if its predecessor has no descriptor.
4. GET `<repository_href>path_index_status/`. `ready` means the latest completed
   version has a compatible descriptor; `manifest_digest` identifies it. This is
   not a check of current S3 availability or pod cache warmth. `error` describes an
   incompatible descriptor. Failed write details are on the modify task.
5. Exercise replacements, removals, bulk modify, directory crawls, retention, and
   pinned versions in staging. Set mode to `serve` to use warm indexed responses.
   Cold requests still use DB fallback, so warm pods before admitting a large CDN
   request rate. Index-aware readiness and traffic admission remain deployment work.

`build_path_index` accepts an optional `repository_version` query parameter with a
retained version HREF or PRN. It does not move the latest version or alter content.
An existing compatible descriptor is verified and reused. Missing generated pages
must be repaired in a new version before building.

POST `<repository_href>compact_path_index/` to rewrite the latest index into a new
base ahead of the segment cap. It reserves the repository, uploads a digest-addressed
checkpoint, then replaces only the version's descriptor. Existing readers can finish
using the old manifest. Automatic equal-level merging and a cap-triggered full rebase
also occur during finalization. Compaction may download the predecessor's complete
view and needs extra disk space. These operations can lengthen the repository queue.

Both POST actions require repository repair and view permissions; the status action
requires view permission. Customized access policies must grant these actions.

Rollback sets mode to `off` or removes the `path_index` label. It does not change the
independent HTML option or delete index objects. Re-enabling after unindexed versions
causes a new baseline on the next change, or an explicit build can populate it first.
For changed storage identity or corrupt immutable objects, use a fresh S3 prefix and
rebuild the latest completed version before modifying it. Never overwrite immutable
objects to repair them.

Earlier revisions of this draft used experimental journal tables and a scheduled
`maven-path-index-reconcile` task. They are not an upgrade target: on a test deployment,
stop writers, use that old revision to migrate Maven back to `0014`, and remove its
reconciliation schedule before installing this revision. Preserve ordinary Pulp
content, use a fresh index prefix, and rebuild. This revision adds no DB migration.

## Settings

All names below have the prefix `MAVEN_PATH_INDEX_`.

| Setting | Default | Purpose |
| --- | --- | --- |
| `MODE` | `"off"` | `off`, `shadow`, or `serve` |
| `S3_BUCKET` | `""` | Required index bucket |
| `S3_PREFIX` | `"maven-path-index"` | Isolated immutable-object namespace |
| `S3_ENDPOINT` / `S3_REGION` | `None` | boto3 endpoint/region overrides |
| `CACHE_DIR` | `"/var/lib/pulp/path-index-cache"` | Shared local disk within a pod |
| `CACHE_BYTES` | 4 GiB | Combined segment and HTML cache allowance |
| `HTML_BYTES` | 64 MiB | Reserved cache portion for generated pages |
| `MAX_VIEWS` | 8 | Maximum mapped views per content process |
| `BUILD_WORKERS` | 2 | Maximum concurrent view preparations per process |
| `REFRESH_SECONDS` | 2 | Descriptor TTL and preparation retry interval |
| `WORK_DIR` | `None` | Additional builder scratch, default under the cache |
| `REDIRECT_THRESHOLD` | `None` | Optional S3 redirect threshold in bytes |

Workers sharing a directory must use identical budgets. Use a disk-backed Kubernetes
`emptyDir` shared by the content workers in each pod. Boto3 clients are created after
forking. The known hosted large-file redirect threshold is `1_700_000_000` bytes;
configure it explicitly when that policy is required. Otherwise the domain's
`redirect_to_object_storage` setting controls S3 redirects. Validate against the
actual hosted image and middleware before rollout.

## HTML delivery and HTTP

Generated HTML remains ordinary `MavenIndexPage` content. Indexed delivery adds a
shared local page cache with per-file locks and reader pins. A page larger than the
budget, or a cache full of active readers, streams from artifact storage.

Responses use artifact SHA-256 ETags and membership Last-Modified dates. Conditional
requests and HEAD can avoid artifact I/O. Public `ArtifactResponse` supplies artifact
streaming and ranges; signed S3 URLs preserve the request method. HTML ranges may
receive a full 200 response. All indexed paths use
`public, max-age=0, must-revalidate`, since Pulp can replace release-looking paths too.

## Disk and operational limits

The segment allowance is `CACHE_BYTES - HTML_BYTES`. A pod-wide fill lock bounds
concurrent downloads, and each complete view holds shared locks against eviction.
Budget for old and new views during switches; a full cache makes a new view fall back
to the DB without evicting active files.

Build sort runs and compaction outputs are **additional** to the cache allowance.
`WORK_DIR` must share a filesystem with the cache because compaction uses hard links.
Provide a volume quota/ephemeral-storage limit with headroom for active builds,
compaction, lock files, and filesystem overhead. The application cache budget is not
a hard limit on total scratch usage. Disk-full/build errors fail modify safely;
reader download errors fall back. A killed build can leave scratch behind. Remove
abandoned scratch only after its writer stops, and discard an entire pod cache only
with its workers stopped. Lock inodes are intentionally not removed while in use.

Monitor modify queue time and failures, S3 latency, cache bytes/inodes, and DB fallback
load. Reader preparation failures log at DEBUG in `pulp_maven.app.path_index.cache`.
S3 storage is a write dependency for opted-in repositories. There is no separate
publisher to absorb an outage, and the initial scan or a full rebase can hold the
repository reservation for a substantial period.

## Validation and capacity

PostgreSQL lifecycle tests exercise the public `add_and_remove` task used by modify,
including a batch of 200 orphan uploads, retention, S3 failure, DB failure after
upload, backfill, no-ops, and compaction. A four-process test verifies shared segment
downloads. HTTP tests cover validators, HEAD, ranges, redirects, and HTML cache pins.
The engine's optional S3 wire tests exercise real conditional requests.

These tests establish correctness, not production throughput. The 20M-artifact
engine measurements do not cover core membership/counting, Maven metadata, HTML
generation, S3 latency, or task queueing. Measure the combined deployment with its
actual repository size and burst workload before claiming a sustained upload rate.
