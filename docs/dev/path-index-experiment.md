# Binary path-index experiment

This is the first implementation slice of [PULP-2423](https://redhat.atlassian.net/browse/PULP-2423).
It implements and tests the file engine in `pulp_maven.app.path_index`. It does not
enable an index in repository finalization, change content responses, register an
auth endpoint, or change HTML generation. No pulpcore changes are required to use
the engine. It now includes an S3 backend and a process-coordinated local download
cache, so shared EFS is not required. See [S3 storage and worker coordination](path-index-s3.md)
for the deployment contract. Remaining integration work and deployment gates are listed below.

The experiment tests whether small per-version changes can replace a full sorted
file rewrite on every upload. This matters for a repository receiving 10,000+
uploads per day and bursts of about 200 requests. It does not establish that core
version creation or the deployment's authentication path can sustain those rates.

## Running the experiment

Use an environment with pulp-maven installed. The file engine needs no Django
settings, database, Redis, or S3 connection.

```console
python -m pulp_maven.app.path_index.benchmark \
  --directory /path/to/benchmark-storage \
  --entries 1000000 --updates 1000 --paths-per-update 100 --lookups 10000
```

Repeat at 5,000,000 and 20,000,000 entries. For sustained change-file testing, use
`--updates 10000`. Each run creates a fresh UUID namespace under the supplied
directory and leaves its files for inspection. Use disposable benchmark storage;
the command does not clean up runs or enforce a disk budget. It stores synthetic
index records only, not artifact payloads. Temporary sort runs use the system
temporary directory, configurable through `TMPDIR` before starting Python.
The entry count is the number of binary records, not a count of actual Maven
artifacts. A repository's directory aliases add records too.

Output is JSON Lines. The final record reports initial build time, per-update
latencies, compaction time, lookup latencies, and bytes written. Updates change 100
paths in the example, with an 80/20 mix of upserts and deletions. Each upsert carries
a new digest, size, and timestamp. Lookups validate results against the synthetic
history. The default compactor runs serially every eight updates; this is not an
upload-concurrency or background-task benchmark.

`segment_bytes_written` includes the base, every delta, and intermediate compaction
outputs. `manifest_bytes_written` includes version manifests and checkpoints. Both
measure application payload writes, including duplicate output attempts, not NFS
traffic, physical disk writes, filesystem metadata, temporary sort I/O, database
WAL, or artifact uploads. `flat_rewrite_bytes_for_comparison` is an analytical
baseline, not a separately timed full-rewrite run. Peak RSS includes touched mmap
pages; it is not the external sort's private-memory footprint.

Measure S3 uploads and cold pod downloads before using the results for deployment
sizing. Post-build lookups benefit from page cache. Local disk, tmpfs, a cold pod
cache, and several content workers sharing a pod are different workloads. Do not
drop caches on a shared host to manufacture a cold benchmark. This command measures
the local file engine, not S3 transfer latency.

### Local measurements

Single development runs on Linux, Python 3.14.7, and local Btrfs produced the
following results. Temporary sorting used tmpfs. Each update changed 100 paths.
The benchmark allowed 32 segments and compacted every eight updates. Lookup
latencies were measured at the final view, not at the maximum transient run count.

| Records | Updates | Initial build | Update p99 | Final-view miss p99 | Writes after base | Maximum / final segments |
| --- | --- | --- | --- | --- | --- | --- |
| 1M | 1,000 | 3.1 s | 12.16 ms | 49.80 microseconds | 51.93 MB | 15 / 7 |
| 5M | 1,000 | 19.5 s | 11.58 ms | 63.12 microseconds | 51.93 MB | 15 / 7 |
| 20M | 1,000 | 80.4 s | 11.91 ms | 62.22 microseconds | 51.93 MB | 15 / 7 |
| 20M | 10,000 | 80.0 s | 12.34 ms | 67.58 microseconds | 752.86 MB | 19 / 6 |

Writes after the base include delta payloads, intermediate compaction outputs,
and manifests/checkpoints. The 10,000-update run wrote 54.72 MB of raw deltas;
compaction and manifests account for the rest. Compaction took 77.85 seconds in
that serial run, separately from foreground update times. Rewriting the 20M-record
base for every update would write approximately 12.8 TB, excluding reads and
filesystem overhead. This comparison demonstrates reduced application write
volume; it does not predict S3 billing or end-to-end upload throughput.

The measurements also show that the issue's proposed 1-5 microsecond lookup target
is not established by this Python prototype. Review the additional searches against
the write savings, and measure worst-bound views and the S3-backed pod cache before choosing
production limits. Full observations are in the
[JSON report](path-index-benchmark-results.json).

## API and version views

```python
from uuid import uuid4

from pulp_maven.app.path_index.format import Entry, path_hash
from pulp_maven.app.path_index.store import IndexStore

store = IndexStore("/path/to/indexes", str(uuid4()), str(uuid4()))
first = store.create(
    str(uuid4()),
    [Entry.for_path("lib.jar", "ab" * 32, 1024, 1700000000)],
)
second = store.update(
    str(uuid4()), first,
    entries=[Entry.for_path("new.jar", "cd" * 32, 2048, 1700000001)],
    removed=[path_hash("lib.jar")],
)
with store.open(second, verify=True) as view:
    assert view.lookup("lib.jar") is None
    assert view.lookup("new.jar").size == 2048
```

The first two identities scope the domain and repository. The integration layer
must supply real Pulp identities, establish version completion, and extract
records from the correct membership interval. The engine does not query or infer
database state. Paths are exact distribution-relative Unicode strings encoded as
UTF-8 before hashing. URL decoding and normalization belong to the future shared
request/extraction layer.

`artifact_entries` produces a file entry and, for `index.html`, a directory alias
with identical artifact metadata. Root `index.html` aliases the empty string.
Callers must remove or replace aliases along with their backing pages. Multiple
input records with one path hash are rejected; extraction must resolve duplicate
paths and hash collisions. An upsert wins when the same key also appears in the
`removed` input. Each individual input must contain unique keys.

A view opens every referenced segment before allowing lookups. Missing files,
malformed structures, broken lineage, and mismatched scope raise exceptions; they
are not interpreted as an absent artifact. A successful lookup returns an Entry
or None. The future integration must distinguish these errors from an authoritative
miss and apply the repository's on-demand/fallback policy.

Retain a view for repeated lookups and close it after concurrent users finish.
The local engine does not supply an asyncio adapter or a worker mapping cache.
The S3 adapter adds a disk cache with process locks and eviction of unused files;
it still requires callers to retain and close views. mmap faults, downloads, and
file operations may block. Do not run this synchronous API directly on the
content app's event-loop thread.

## On-disk contract

Files are under `<directory>/<domain_uuid>/<repository_uuid>/`:

- `segments/<sha256>.bin`: immutable base or delta payloads.
- `versions/<version_uuid>.json`: immutable manifests published after their new segments.
- `checkpoints/<manifest_sha256>.json`: equivalent compacted version views.
- `staging/`: unpublished temporary output.
- `publish.lock`: a persistent inode for writer coordination.

All integers in binary files use big-endian encoding. Hashes sort in byte order.

| File section | Bytes | Meaning |
| --- | --- | --- |
| Base header | 16 | `PULPXIDX`, version byte 1, seven zero bytes |
| Base record | 64 | Path SHA-256 prefix 16, artifact SHA-256 32, unsigned size 8, signed epoch seconds 8 |
| Delta header | 16 | `PULPXDLT`, version byte 1, seven zero bytes |
| Delta counts | 16 | Unsigned 64-bit upsert count, unsigned 64-bit deletion count |
| Delta upserts | 64 each | Strictly sorted base-format records |
| Delta deletions | 16 each | Strictly sorted path hashes, disjoint from upserts |

Manifests record a format version, domain/repository/version identities, the
immediate parent identity, and an oldest-to-newest segment list. Each reference
contains the digest, type, byte size, lineage start/end identities, and generation
level. The first segment must be a base; remaining segments must form a contiguous
acyclic chain ending at the target version. Manifests are limited to 64 KiB and at
most 32 segments. Stores default to a lower limit of 16.

Header validation checks magic, format, reserved bytes, counts, and alignment. It
does not detect arbitrary payload corruption. `store.open(..., verify=True)` also
audits complete file digests, ordering, duplicate keys, and disjoint sections.
That audit reads the full payload and is unsuitable for each request. Files must
come from a trusted writer and never be modified or truncated while mapped.
Verification policy for long-lived content-worker mappings remains an integration
decision; these experimental defaults must not silently become production policy.

The initial sort uses fixed-size chunks and bounded fan-in across multiple merge
passes. Temporary sort files are cleaned up on normal completion and exceptions.
Publication fsyncs output before rename and then fsyncs its directory. A persistent
flock serializes publication; it does not serialize the entire build. Existing
immutable files must have identical bytes for an idempotent retry. Conflicting
version publications raise `PublicationConflict`. Readers see no partial manifest.
With the S3 backend, these POSIX operations are local to one pod. Conditional S3
object creation coordinates publication across pods; manifests are uploaded last.

## Compaction and retention

`store.update` writes a delta and manifest without reading the base. Call
`store.compact(manifest)` outside the upload path to merge adjacent delta runs at
equal generation levels. A level represents the number of merged update
generations, not bytes. Uneven upload sizes may warrant a different measured
policy. Newest changes win, and deletion markers remain until all older values
they hide have been included in a full rebase.

Compaction persists a checkpoint and returns its Manifest. It never replaces the
original version manifest. Pass this returned checkpoint as `previous` for future
updates. Its digest is SHA-256 of `manifest.encode()` and can be used with
`read_checkpoint` after a restart. The integration layer must select and retain
checkpoints and prove any intervening deltas cover the selected lineage.

`store.compact(manifest, rebase=True)` explicitly folds the entire view into a new
base. It removes deletion markers and reads/writes O(N) bytes. A normal update never
triggers this operation implicitly. At the run limit, update raises
`NeedsCompaction`; callers must arrange compaction or an explicit fallback policy.

There is no automatic garbage collection in this slice. Retained versions and
checkpoints share segments. Version deletion alone does not make its base safe to
unlink. A future collector must account for retained manifests, usable checkpoints,
active builders, and reader/cache grace periods. Interrupted processes can leave
staging files and unreferenced segments. Do not unlink `publish.lock` while writers
may hold it. Use an isolated namespace for this development experiment.

## Validation and next gates

The standalone tests cover byte fixtures, UTF-8, directory aliases, bounded sorting,
replacements, tombstones, randomized histories, retained versions, partial writes,
conflicting processes, malformed files, scope/lineage validation, and compaction.
A separate Django smoke test verifies that Maven can register a route through
`pulpcore.plugin.content.app` without database access. It does not exercise actual
authentication, streaming, or production middleware ordering.

```console
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q \
  pulp_maven/tests/unit/test_path_index_format.py \
  pulp_maven/tests/unit/test_path_index.py \
  pulp_maven/tests/unit/test_path_index_benchmark.py \
  pulp_maven/tests/unit/test_path_index_plugin.py
```

Before connecting the writer to uploads, benchmark minimal core version commits
on an isolated Pulp instance with representative repository sizes. Record full
membership-array processing, counts queries, retention cost, Python memory, WAL,
and exclusive-resource duration. Then measure the added Maven finalization cost.
The local environment used for initial development had no configured Pulp database,
so that test remains outstanding. Binary-engine timings do not substitute for it.

Next implementation work is version-scoped extraction and initial-build tasks,
incremental directory summaries, mapping lifecycle, then guarded auth/origin
integration. The deployed guard, Akamai contract, cold S3/cache behavior, and 200-upload burst
capacity are still deployment gates. This slice adds no schema migrations or
production settings.
