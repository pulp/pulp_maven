# S3 storage and worker coordination

See the [glossary](path-index-glossary.md) for storage, locking, memory, and cache terms.

The experimental index can use S3 as its durable store. Every pod keeps a
disposable local copy of the segments it reads. There is no EFS requirement and
no need to download one copy per gunicorn worker. This document describes the storage
backend itself. See the [experimental Maven integration](path-index-integration.md)
for repository hooks, synchronous publication, and content serving.

## Using the backend

Install the optional SDK with `pip install 'pulp-maven[path-index-s3]'`. Create the
boto3 client after forking the worker, using the deployment's credentials and
region. A standard S3 general-purpose bucket supporting conditional `PutObject`
is required. The adapter never falls back to unconditional writes when an endpoint
lacks that support.

```python
import boto3
from botocore.config import Config

from pulp_maven.app.path_index.s3 import S3IndexStore

store = S3IndexStore(
    boto3.client(
        "s3",
        config=Config(
            connect_timeout=3,
            read_timeout=60,
            retries={"mode": "standard", "total_max_attempts": 3},
        ),
    ),
    bucket="my-pulp-indexes",
    prefix="maven-path-index/v1",
    cache_directory="/var/cache/pulp-path-index",
    domain_id=domain_id,
    repository_id=repository_id,
    max_cache_bytes=4 * 1024**3,
)

# Same record/manifest API as IndexStore. The caller supplies real Pulp identities.
first = store.create(first_version_id, initial_records)
second = store.update(next_version_id, first, changed_records, removed_path_hashes)

# On a content pod, run this in a background worker before admitting traffic.
manifest = store.read_version(next_version_id)
store.warm(manifest)
with store.open(manifest) as view:
    entry = view.lookup("com/example/library/1.0/library-1.0.jar")
```

This is a synchronous API. Run builds, downloads, full integrity checks, and
view opening/closing outside the aiohttp event-loop thread, with bounded worker
concurrency. The application must manage cancellation and retain views until all
requests using them have finished. The Maven integration supplies a process-level
mapping LRU. For direct engine use, retain the fetched immutable Manifest and view
for hot-path lookups; `read_version` itself makes an S3 GET each time it is called.

## Across pods: immutable S3 publication

Keys have the form
`<prefix>/<domain_uuid>/<repository_uuid>/{segments,versions,checkpoints}/...`.
Segment names are SHA-256 digests; version manifests use the version UUID;
checkpoints use the manifest digest. The binary and manifest formats are unchanged.

A builder creates files in private scratch space, uploads any new referenced
segments, and uploads the manifest last. An update uploads its delta and small
manifest, without downloading or rewriting the base. Existing segments require
only bounded HEAD requests. A missing predecessor prevents publication of the new
manifest. Compaction obtains verified local copies, reuses their inodes with hard
links, and uploads only the final new segments plus a checkpoint.

Every PUT uses `If-None-Match: *`, a SHA-256 checksum, and digest metadata. A HEAD
before upload avoids redundant transfers when possible, but the conditional PUT
is the operation that resolves races between different builders. Identical retries
succeed; a different manifest for an already published version raises
`PublicationConflict`. HTTP 409 conflicts get at most three application attempts;
other failures remain unavailable and can be retried by the task layer. A crashed
builder can leave unreferenced segments, but cannot expose half a manifest or
overwrite a completed version. See [AWS conditional-write semantics](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).

There is no mutable "latest" S3 key. The application must resolve the exact serving
version and bind extraction to its completed database state. Conditional writes
coordinate publication correctness; they do not serialize database extraction or
prevent two pods from doing redundant initial builds. When repository hooks are
added, use Pulp's existing task resource reservations to schedule one builder or
compactor for the repository. No expiring distributed lock is introduced here.

The backend uses bounded-memory single-object uploads, currently limited to 5 GiB
per segment. A 22M-record base is about 1.4 GB and fits. Larger segments fail
explicitly; multipart upload and its cleanup are future work. Build scratch is
removed on normal completion and exceptions. Process termination can leave scratch
directories, so use disposable storage and clean abandoned builds only after their
processes have stopped.

## Within a pod: one download and shared mmap pages

All content worker processes in one pod must use the same disk-backed local volume
and cache directory, for example a Kubernetes `emptyDir` mounted into each content
container. Different pods use independent volumes. Configure the same cache budget
in every process sharing a directory. Cache namespaces include endpoint, bucket,
prefix, domain, and repository to avoid mixing deployments.

1. A persistent file lock per segment coordinates worker processes, including
   processes with different Python event loops. An asyncio lock alone is insufficient.
2. A missing segment is downloaded into a private temporary file. The downloader
   validates its size, complete digest, format, ordering, and deletion invariants,
   then fsyncs and atomically renames the file into the cache.
3. Waiting workers reuse that completed file. Each worker mmaps the same inode,
   so the kernel can share its page-cache pages across the workers. Each pod still
   needs its own copy; S3 downloads are once per pod per resident segment.
4. Open views hold shared locks. Eviction takes nonblocking exclusive locks and
   skips files still in use. Closing a view closes its mappings before releasing
   those locks. Killing a worker releases its OS locks automatically.

A second persistent lock serializes cold fills within a pod. This bounds temporary
download space and keeps many cold requests from all consuming bandwidth at once.
Before filling, it removes abandoned download temporary files and evicts unused
segments by approximate last-view-open time. It never deletes lock files. No file
is truncated or overwritten while mapped. Large downloads do not hold locks on
another pod's cache.

The cache budget covers downloaded segments across all repositories/endpoints
under that cache directory. It excludes lock metadata, build scratch, external
sort runs, and process memory. Default build scratch is under `builds/` in the
cache directory; an explicit `work_directory` must be on the same filesystem to
support compaction hard links. Size the pod's disk limit for cache **plus** active
builds and compaction outputs. Prefer disk-backed `emptyDir`, since a memory-backed
volume would consume the pod memory budget for the entire cache.

If all eviction candidates are in use, a new view raises `CacheFull` rather than
exceeding the budget or disrupting existing readers. A view larger than the budget
is rejected before downloading anything. Lock waits are bounded by `lock_timeout`
(30 seconds by default). Downloads also need explicit SDK connection/read timeouts.
Run `store.prune()` after reducing a cache budget; it still respects open views.

## Cold pods, failures, and retention

A 1.4 GB initial download plus full verification can take far longer than an auth
request's budget. Prewarm the current serving versions on each pod before routing
traffic to it. When a new version shares a cached base, warming it downloads only
missing deltas or compaction outputs. Warming does not pin files permanently; keep
the current serving views open, and budget for old and new views during a switch.

Missing manifests/segments, S3 access failures, lock timeouts, failed downloads,
invalid files, and a full cache do not mean "artifact not found." They raise errors.
Only a lookup on a complete opened view may return `None`. The eventual content
adapter must apply the existing DB/on-demand fallback policy to unavailable indexes
and avoid interpreting those errors as a 404. Missing manifests are not negatively
cached, so an initial build can become usable on the next attempt.

Every downloaded segment receives a complete integrity audit before local
publication. Warm opens validate structure and the expected size; `verify=True`
can repeat the full audit. Local cache files must be writable only by trusted
index processes. S3 writers are also trusted; digest metadata is used to recognize
already uploaded objects. Restrict the dedicated prefix to the intended reader
and writer roles. Readers need GET access; writers need GET/HEAD and PUT access.
This backend does not delete S3 objects or require object-listing permissions.
AWS can return 403 for a missing key when the writer lacks `s3:ListBucket`. For a
locally built object, the adapter then attempts the conditional PUT; it still
requires readable metadata after a conflict. Reads and predecessor checks continue
to fail on 403. See [AWS HEAD permissions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html).

There is no automatic S3 retention policy yet. Do not expire shared base/delta
objects by age: retained versions and checkpoints can still reference them. A
future collector must trace retained manifests and account for builders. Local
eviction is safe because S3 remains the durable source.

## Validation

Unit tests cover delta-only updates, immutable retries/conflicts, two publisher
processes using different pod directories, four content workers per pod sharing a
single GET, killed downloaders, corrupt/partial downloads, bounded lock waits, and
eviction with open readers. Optional wire tests cover a real S3-compatible service:

```console
# Set test-only AWS credentials separately. The endpoint must be disposable.
PULP_PATH_INDEX_TEST_S3_ENDPOINT=http://127.0.0.1:9000 \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q \
  pulp_maven/tests/unit/test_path_index_s3_service.py
```

Wire tests create and remove fresh test buckets; these tests need additional
bucket-management permissions beyond the deployed adapter. Local MinIO validation
does not establish hosted AWS throughput. The earlier 1M/5M/20M measurements remain
local file-engine measurements. Measure cold pod startup, warmed lookup latency,
S3 transfer volume, cache pressure, and concurrent upload/auth traffic on the
hosted deployment before using this feature in the request path.
