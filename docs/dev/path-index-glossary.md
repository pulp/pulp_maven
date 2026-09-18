# Path-index glossary

Plain-language definitions for the [integration guide](path-index-integration.md),
[index engine](path-index-experiment.md), [S3 backend](path-index-s3.md), and
[HTML directory pages](../user/guides/index-pages.md). Terms describe this experiment;
their presence here does not mean every proposed deployment feature is implemented.
The integration guide records current behavior and settings.

Use these sections to find a term:

- [Enabling and disabling the index](#enabling-and-disabling-the-index)
- [Pulp and Maven](#pulp-and-maven)
- [Version changes and publication](#version-changes-and-publication)
- [Index files and lookups](#index-files-and-lookups)
- [Directory pages](#directory-pages)
- [Storage and publication](#storage-and-publication)
- [Workers, memory, and caches](#workers-memory-and-caches)
- [HTTP and the CDN](#http-and-the-cdn)
- [Measurements and testing](#measurements-and-testing)

## Enabling and disabling the index

The repository label `path_index=true` is the only feature switch. Its domain must
use S3 storage. The earlier global `off`, `shadow`, and `serve` modes were removed.

| State | Meaning |
| --- | --- |
| Label absent or not `"true"` | The integration is disabled. Pulp uses its existing content resolver. Stored index data is not deleted. |
| Label is `"true"` | Publish indexes synchronously and use complete prepared views for content requests. Unavailable views use the existing resolver. S3 publication failure fails modify. Authentication and content guards still run. |

The label does not enable the independent incremental HTML generator.

## Pulp and Maven

| Term | Meaning |
| --- | --- |
| API | An interface programs use to request operations. The Pulp API manages repositories and content; a content URL delivers a file or directory page. |
| Artifact / `Artifact` | The stored bytes of a file, with a size and checksums. Several content units can refer to the same artifact. |
| Authentication / authorization | Authentication establishes who a requester is. Authorization decides what that requester may access. |
| Base path / distribution-relative path | The base path locates a distribution in the content URL. The relative path locates a file inside it, such as `com/example/lib/1.0/lib.jar`; this is the string the index hashes. |
| Checkpoint distribution | A Pulp distribution configured to serve checkpoint publications. The experimental content adapter skips this mode. This is separate from an index compaction checkpoint. |
| Content guard / redirect guard | A content guard checks permission to access a distribution. A redirect guard has a signed-URL contract; the experimental adapter leaves these distributions on the existing flow. |
| Content unit | A Pulp record describing content, such as a Maven artifact, metadata file, or generated page. It can belong to multiple repositories and versions. |
| `ContentArtifact` | The record connecting a content unit to an artifact and its relative path. An unresolved on-demand file can have a path without a locally stored artifact yet. |
| Distribution | The Pulp object that exposes content at a base path. It can follow a repository's latest version or point at a particular version. |
| Domain / scope | A domain separates Pulp resources and storage configuration. Index scope identifies the domain and repository a file belongs to, preventing reuse across unrelated repositories. |
| GAV / Maven coordinates | Group ID, artifact ID, and Maven version. For example, `com.example:lib:1.0` identifies a package version. This Maven version is separate from a Pulp repository version. |
| HREF / `repository_href` | A resource's API URL, such as the URL identifying one Maven repository. Documentation appends action names to this URL. |
| JAR / POM | A JAR is a Java archive. A POM is Maven's Project Object Model XML file describing a package and its dependencies. Both can be stored as Maven artifacts. |
| Maven deploy API | The Maven-compatible upload interface. Its immediate repository update can return HTTP 429 when the repository reservation is busy, whereas deferred Pulp API tasks can queue. |
| `MavenArtifact` / `MavenPackage` | `MavenArtifact` describes an individual Maven file. `MavenPackage` represents the logical package at a GAV, grouping the package information derived from files. |
| `MavenMetadata` / `maven-metadata.xml` | Maven's package/version discovery metadata and related checksum files. These are separate from index manifests, HTTP headers, and S3 object metadata. |
| Membership / `RepositoryContent` | The database record describing when content entered a repository and when it was removed. The index uses its creation time for Last-Modified. |
| Membership interval | The span of repository versions in which a content unit is present, determined by its added and removed versions. |
| Membership array / version counts | Core's stored list of content IDs and its content counts for a repository version. Maintaining these can cost time independently of binary-index updates. |
| On-demand content | Content whose metadata/path is known but whose file may be fetched from a remote when requested. An incomplete index cannot authoritatively reject these requests. |
| Pinned repository version | A distribution explicitly selects one repository version instead of following the latest. This is different from pinning a local cache file against eviction. |
| PK / UUID / PRN | A PK is a database primary key. A UUID is the identifier format used for repository versions and many Pulp resources. A PRN is a Pulp Resource Name, another way to identify an API resource. |
| `pulp_labels` / opt-in | Repository key/value labels. `path_index=true` enables index publication and serving for that repository. |
| Pulp / pulpcore / pulp-maven | Pulp manages and distributes content. Pulpcore provides shared infrastructure; pulp-maven adds Maven content types and behavior. The experiment lives in pulp-maven. |
| pulp-content / content app | The Pulp service that handles client content requests, including guard checks and file delivery. Its worker processes use the local index cache. |
| Pull-through caching | Fetching requested content from an upstream remote and adding it to Pulp. Distributions with a remote use the existing request flow. |
| Remote | Configuration describing an upstream source from which Pulp can download content. It is different from the S3 store holding this experiment's index files. |
| Repository | A collection of content managed by Pulp. Changes produce repository versions. |
| Repository version / complete version | A snapshot of a repository's content membership. A complete version has successfully finished core's creation process; it is eligible for serving. |
| Selected / target / serving version | The exact repository version a lookup is intended to answer for. A usable index view must describe that version, including all intervening changes. |
| Source of truth / derived state | Repository membership in PostgreSQL determines what content belongs to a version. The index and directory summaries are derived data that can be rebuilt from it. |

## Version changes and publication

| Term | Meaning |
| --- | --- |
| Bootstrap / initial build / baseline | A full scan that creates the first complete index for a selected version. Later versions can use deltas. |
| Capture / extraction | Reading final changed paths and their metadata before retention alters membership history. Changes go directly into a binary delta. |
| Manifest descriptor / change token | The small value in RepositoryVersion.info containing format, storage profile, manifest digest, and checkpoint flag. The version UUID and digest identify the selected view, including after compaction. |
| Capture receipt / confirmation | Terms from the removed journal design. The current descriptor is saved with version completion instead. |
| Chunk | A bounded batch processed in memory, a sort run, or part of a streamed response. Earlier drafts also used DB journal chunks, now removed. |
| Cursor | A saved progress marker. The separate HTML optimization tracks the version represented by its directory summary. |
| Dispatch / enqueue | Asking Pulp to run a task. Modify publishes its own index; initial build and operator-requested compaction are separate tasks. |
| Durable journal / shadow journal | The former DB record of changes awaiting publication. Removed: deltas now reach S3 before a version completes. |
| Exclusive resource / repository reservation | Tasks reserving the same repository run one at a time. Modify, bootstrap, and maintenance compaction all use it. |
| Finalization / finalize_new_version | Maven work before core completes a version, including metadata, HTML, and synchronous index publication. |
| Foreign key | A database relationship to another row. S3 manifests reference segments independently of DB retention. |
| Full scan / incremental update | A full scan reads all relevant version content. Incremental extraction reads changed paths. Bootstrap scans the full version; normal index updates use deltas. |
| Garbage collection / collector | Deleting data no longer needed. Automatic S3 index collection is not implemented; shared segments must remain while referenced. |
| Gap / broken lineage | Missing or incompatible history. A predecessor without a descriptor requires a baseline; missing or corrupt referenced objects fail publication. |
| Lag / journal suffix | Terms from the former asynchronous publisher. New versions complete after upload, although reader TTLs and cold caches can delay adoption. |
| No-op version | An attempted update with no effective content change. Core discards it; the index does not publish it. |
| Orphan | Pulp content or artifacts without a repository reference, including newly uploaded content awaiting modify. Normal orphan cleanup is separate from S3 index collection. |
| Prepared / confirmed / abandoned | States from the removed DB journal. The current integration does not use them. |
| Publisher / builder / compactor | The builder creates files, the publisher uploads them, and the compactor merges segments. Finalization can perform all three. |
| published_version / latest_version | The old published_version field is removed. Current status reports latest_version, ready, and manifest_digest. Ready means a compatible descriptor exists, not that pods are warm. |
| Reconciliation | Recovery from the removed journal design. There is no reconciliation schedule now; retry failed modify tasks or run an explicit build. |
| Retention / retain_repo_versions | Core's policy for keeping versions. Deleting a version does not make its shared index segments safe to delete. |
| Rollback | Disabling the binary index or removing its opt-in. Index objects remain, and the independent HTML setting is unchanged. |
| Squashing membership history | Adjusting membership records during retention. Later added()/removed() queries may no longer reconstruct the original delta; publication happens first. |
| summary_pending | A former combined-integration field. The independent HTML summary uses a version UUID cursor and rebuilds when it does not match the previous completed version. |
| Transaction / commit | A DB transaction groups changes so they commit or roll back together. S3 writes cannot be part of that transaction; the descriptor makes uploaded objects visible only after DB completion. |

## Index files and lookups

| Term | Meaning |
| --- | --- |
| Authoritative miss / `ABSENT` | A complete view proves the requested path does not exist in the selected version. Only this result can justify an indexed 404. |
| Base / base segment | A binary file containing the complete indexed path set at one starting version. Later changes can be represented by additional delta segments. |
| Big-endian / signed / unsigned | Binary integer conventions. Big-endian stores the most significant byte first. Signed integers can be negative; unsigned integers cannot. The format specifies these rules so readers agree. |
| Binary path index | Files that map a hash of a relative path to an artifact digest, size, and timestamp, allowing lookups without querying repository membership for each path. |
| Binary search | Looking up a key in sorted records by repeatedly halving the possible range. The engine searches path hashes, rather than comparing every record. |
| Bloom filter | A compact membership check that can rule out some absent paths but can also return false positives. Pulp Maven's existing Bloom-filter support is separate from this index, which returns artifact metadata from a complete view. |
| Checkpoint / compaction checkpoint | An immutable manifest with a different segment layout for the same version. Maintenance records its digest after upload. |
| Collision | Two distinct inputs producing the same hash. A path hash is an identifier for lookup, not a reversible encoding of the original path. |
| Compaction / merge | Combining segments while preserving the newest result for each path. Merging deltas reduces the number of files a lookup must search. |
| Contiguous / acyclic lineage | A version chain with no missing predecessor links and no loops. It must end at the selected version. |
| Delta / change file / delta segment | A binary file holding changes and deletion markers. The finalizer uploads it before the version completes. |
| Digest / checksum / SHA-256 | A fingerprint computed from bytes. Full SHA-256 digests identify artifacts, segments, and manifests and detect damaged data. |
| Entry / record | One fixed-size binary item: path hash, full artifact SHA-256, artifact size, and Last-Modified timestamp. Each normal entry is 64 bytes; a deletion stores only its path hash. |
| Epoch seconds / UTC | Seconds measured from the Unix epoch, 1970-01-01 at 00:00:00 UTC. UTC is Coordinated Universal Time, independent of local time zones. The binary index stores membership dates as epoch seconds. |
| Exact-version view / complete view | An opened base and ordered changes that together describe exactly the selected repository version. "View" here is an in-process reader, not a SQL view. |
| External sort / sort run / fan-in | External sorting writes sorted chunks to temporary files rather than holding the whole input in memory. A run is one sorted sequence. Fan-in limits how many runs a merge reads at once. |
| Fallback / existing resolver | Returning a request to Pulp's existing content lookup when the indexed route cannot answer it. Fallback can involve database queries and normal on-demand behavior. |
| `FOUND` | A lookup returned an entry from a complete view. The caller can use its metadata to deliver the artifact or handle a conditional request. |
| Generation level | A compaction tier recording merge history. Equal-level neighboring deltas can be merged into a higher level. Level measures generations of updates, not file bytes. |
| Header / magic / format version | The identifying bytes at the start of an index file. Magic identifies the file type; the format version identifies the encoding. Header validation alone does not verify every payload byte. |
| Alignment / reserved bytes | Alignment checks whether file lengths fit the record layout without partial records. Reserved bytes leave room for future format changes; this version requires them to be zero. |
| Integrity audit / verification | Checking file digests, record order, duplicate keys, and structural rules. A full audit reads the complete file and is more expensive than a lookup. |
| Lineage / parent / predecessor / ancestor | The version history a manifest describes. The parent or predecessor is the immediately preceding version; an ancestor is any earlier version on that chain. |
| Manifest / segment reference | A small JSON description of a version's ordered segments. A reference records a segment's digest, size, type, lineage, and generation level. The manifest contains no artifact payloads. |
| Overlay / journal overlay | A removed reader design that applied DB journal changes locally to an older index. Readers now load the completed version manifest directly. |
| Path hash / sort key | The first 16 bytes of SHA-256 of the exact UTF-8 relative path. Records sort by this key. The artifact's full SHA-256 is a separate field. |
| Payload | The actual bytes in a file or message, such as an artifact or binary index records. |
| Rebase / full rebase | In the index engine, combining the complete view into a new base. It reads and writes the full indexed set and can discard deletion markers. It does not change repository content. |
| Segment / run limit | A segment is one immutable base or delta file. A segment limit bounds how many files one view may reference before compaction or fallback is needed. |
| Tombstone / deletion marker | A record that a path was removed. It hides an older entry for that path; a reader must not resurrect the old file by continuing to an older segment. |
| `UNAVAILABLE` | Required index data is missing, invalid, cold, or over budget. The existing resolver must handle the request; this is not an authoritative miss. |
| Upsert | An add-or-replace operation for a path. When the same update supplies both a deletion and an upsert for a key, the upsert supplies the resulting entry. |
| UTF-8 / Unicode / normalization | Unicode represents text; UTF-8 turns that text into bytes for hashing. Normalization or URL decoding can change a path, so extraction and requests must agree on the exact string. The engine does not normalize paths itself. |

For example, V10 contains `a.jar`. The V11 finalizer uploads a delta that removes
`a.jar` and adds `b.jar`, then uploads V11's manifest. Core completes V11 with its
manifest descriptor. A reader uses that exact manifest to find `b.jar` and reject
`a.jar`. If upload fails, V11 does not complete and clients continue using V10.
Deleting V10's DB row later does not make its shared base safe to delete.

## Directory pages

Summary and dirty-directory terms describe the independent HTML optimization.

| Term | Meaning |
| --- | --- |
| Ancestor / descendant / immediate child | For `com/example/lib/a.jar`, `com/` is an ancestor and `a.jar` is a descendant. The immediate child of `com/` is `example/`. Directory summaries store immediate children. |
| Deduplication / page reuse | Reusing existing content when a generated page has the same path and byte digest. An unchanged page can retain its existing repository membership. |
| Directory alias | An extra index entry mapping a directory path to its `index.html` artifact. `com/example/` aliases `com/example/index.html`; the root directory uses an empty relative path. |
| Directory coverage | Every directory required by indexed file paths has its generated page and alias. Without complete coverage, an index could incorrectly reject a valid directory request. |
| Directory listing / crawler | A listing is an HTML page of immediate child files and directories. A crawler repeatedly follows those links, potentially creating many listing requests. |
| Directory summary / direct-child state | Derived DB rows recording each directory's children and their file metadata. They let the generator update a page without querying all descendants. |
| Dirty directory / partial index | A dirty directory needs its HTML checked or regenerated. A partial PostgreSQL index includes only dirty rows, making them cheaper to find. This SQL index is separate from the binary path index. |
| `MavenIndexPage` | A Pulp content unit for pre-generated directory HTML. The associated artifact holds the page bytes, and retained repository versions keep the pages they reference. |
| Pruning directories | Removing empty directory summaries and pages, and then removing their links from parents. This can continue upward when a parent becomes empty too. |
| Render / pre-generate | Rendering turns a directory's child entries into HTML. Pre-generation does this during repository updates so requests can read an existing page. |

## Storage and publication

| Term | Meaning |
| --- | --- |
| Atomic rename / atomic appearance | Publishing a completed local file by changing its name in one filesystem operation. Readers see either no final file or the completed file, rather than partial output. |
| AWS / S3 / object storage | AWS is Amazon Web Services; S3 is its object-storage service. Objects are byte blobs addressed by bucket and key. This experiment stores durable index segments and manifests there. |
| Backend / adapter | A component connecting the index to another service. The S3 backend handles object storage; the content adapter connects lookups to Pulp's request flow. |
| boto3 / botocore / SDK | boto3 is the Python AWS software development kit, built on botocore. The backend uses them for S3 requests, credentials, timeouts, and retries. |
| Bucket / object key / prefix | A bucket holds S3 objects. A key identifies an object inside a bucket. A prefix is a shared beginning of keys used to organize this experiment's objects. |
| Conditional PUT / create-if-absent | An S3 write using `If-None-Match: *` to create an object only if its key is not already present. This coordinates immutable publication between pods. |
| Content-addressed / immutable | Content-addressed names derive from a digest of the bytes. Immutable objects are never changed in place. An immutable artifact does not make its repository path immutable; a later version can map that path to different bytes. |
| Credential chain / role / permissions | The SDK's normal credential discovery and the access granted to those credentials. Index readers and writers need appropriate S3 permissions; the integration does not invent a separate credential system. |
| Durable / disposable | S3 objects and committed version descriptors survive loss of a content pod. Local files are disposable caches. |
| EFS / NFS | EFS is AWS's shared filesystem service, accessed using NFS, the Network File System protocol. Earlier designs proposed EFS; the S3 integration uses separate pod-local caches instead. |
| Endpoint / region | An endpoint is the service address a client contacts. A region selects an AWS location. Tests can use a local S3-compatible endpoint. |
| `fsync` | Asking the filesystem to persist buffered file or directory changes. The local publisher syncs output before exposing its final name. |
| Hard link | Another directory entry for the same filesystem inode and bytes. Compaction uses hard links to reuse cached segment data in its working directory without copying it. Both locations must share a filesystem. |
| Idempotent retry / publication conflict | An idempotent retry repeats an operation with the same result. Re-publishing identical immutable data can succeed; different data for an already claimed version or object is a conflict. |
| Index publication / manifest-last publication | Making new segments durable, then publishing the manifest referring to them, then recording DB progress. It is separate from Pulp's `Publication` model used by some distribution modes. |
| MinIO / S3-compatible | MinIO is the object-storage service used for local wire tests. S3-compatible services implement the relevant API; compatibility tests do not establish production AWS performance. |
| Metadata / object metadata | Information describing data, such as a size or checksum. S3 objects have associated attributes; this backend records a SHA-256 digest there. Those attributes are separate from Maven's metadata files. |
| Multipart upload | Uploading a large object in several separately managed parts. The experimental index backend uses single-object PUTs; multipart support is future work. |
| Namespace | A boundary separating objects or files for different endpoints, buckets, prefixes, domains, or repositories. A fresh namespace allows a new experiment without overwriting immutable objects. |
| POSIX / `flock` | POSIX refers to Unix-style operating-system interfaces. `flock` coordinates cooperating processes through an open file; this experiment uses it for local readers and writers. |
| Profile / storage-profile fingerprint | A hash of domain, storage backend, bucket, location, endpoint, region, and extraction revision. It selects the index namespace and prevents incompatible manifests and views from being reused. Credentials are excluded so rotation does not require rebuilding. |
| Scratch / staging / temporary output | Scratch is working space for sorting and building. Staging holds output not yet published. These files require disk space in addition to retained outputs. |
| S3 GET / HEAD / PUT / LIST / DELETE | Read an object, inspect its metadata, write an object, list keys, or remove an object. The index backend uses GET/HEAD/PUT; it does not require object listing or deletion. |

## Workers, memory, and caches

| Term | Meaning |
| --- | --- |
| Asynchronous / synchronous / event loop | An event loop coordinates requests that can wait for I/O without occupying it continuously. Synchronous work blocks its calling thread. Slow index preparation runs in background threads rather than the content event loop. |
| Backpressure / overload policy | Controlling work when arrivals exceed capacity, for example by queuing or rejecting requests. Cache limits do not themselves provide a deployment-wide traffic policy. |
| Bounded / limit / budget / headroom | Bounded work has configured limits, such as bytes, versions, threads, or segments. A budget is an allowed resource amount. Headroom is spare capacity for downloads, sorting, and old/new views coexisting. |
| Cache hit / cache miss | A hit finds a reusable cached value. A cache miss means it is not cached, which is different from an authoritative index miss proving that an artifact path is absent. |
| Cache invalidation / TTL / staleness | Invalidation stops reuse of a cached value. TTL is its time to live before refresh. Staleness is the time a cached descriptor can lag the current database value. |
| Cancellation | A signal that a request or task is no longer needed. An already running storage call can outlive the caller, so its eventual files and locks still need to be released. |
| Cold / warm / prewarm | Cold means required data or pages are not ready locally. Warm means they are available. Prewarming prepares them before traffic arrives; a file on disk can still have cold OS page-cache pages. |
| Concurrency / serialization | Concurrency allows work to overlap. Serialization makes selected operations run one at a time, for example to prevent several workers downloading the same segment. |
| Container / pod | A container runs an isolated application environment. A Kubernetes pod groups containers that can share volumes. Workers in one pod share this cache directory; other pods have separate caches. |
| `emptyDir` / ephemeral storage | A Kubernetes volume associated with a pod's lifetime. It is suitable for disposable cache data. The experiment uses disk-backed storage and must fit within the pod's storage allowance. |
| Eviction / prune / LRU | Eviction removes reusable local cache data to free space. Prune enforces the cache budget. LRU means least recently used; the disk cache approximates recency by the last view-open time and skips pinned files. |
| Executor / thread pool | Bounded background threads that prepare requested views without each request starting its own download. |
| File descriptor / inode | A file descriptor is a process's open handle. An inode is the filesystem's identity for file data. Workers mapping the same inode can share its kernel page-cache pages. |
| Fill / fill lock | Loading missing data into the cache. A shared fill lock serializes downloads within a pod to enforce a common disk budget. |
| Fork / process / worker | Fork creates a new process from an existing one. Gunicorn content workers are separate processes; Pulp task workers execute background tasks. A thread pool is another form of concurrency inside a process. |
| Grace period | Extra time before removing old data, allowing existing users to finish. A future shared-object collector must account for active readers and caches as well as retained versions. |
| Hot path / per-request work | Work repeated for client requests. Avoiding full scans, S3 downloads, and membership queries here is the reason to retain prepared views. |
| Lease / pin / reader pin | A lease keeps a process-local view from closing during lookup. File pins hold shared locks against eviction while index mappings are active. They do not pin a repository version in core. |
| Local cache / kernel page cache | The local cache consists of downloaded files on pod storage. The kernel page cache consists of their recently used data in RAM. Their disk and memory costs are separate. |
| Mapping / `mmap` | A memory mapping lets a process read a file through virtual memory. Mapping a file does not eagerly read every byte; the OS fetches needed pages. |
| Negative caching | Caching an absence result. It must identify the correct version; an unavailable index cannot safely supply a negative result. The experiment does not add a shared negative-response cache. |
| Page / page fault | A page is a unit of virtual memory. A page fault occurs when a referenced page is not currently accessible and the OS must resolve it, possibly by reading file data. This is unrelated to an HTML page. |
| Readiness / traffic admission | Deciding whether a pod should receive traffic. Being able to serve through DB fallback does not prove its index is warm enough for a large request rate. An index-aware readiness gate remains future work. |
| Redis | A separate fast key/value service used by parts of Pulp. This integration does not add a Redis distribution-descriptor or guard-decision cache. |
| Serving descriptor | Cached repository UUID, version UUID, storage profile, and manifest digest. It contains no content membership list and is refreshed on a per-process TTL. |
| Shared / exclusive / nonblocking lock | Shared locks allow multiple readers. An exclusive lock allows one owner. A nonblocking attempt returns immediately if unavailable, letting eviction skip an active file. |
| Storage pressure / cache pressure | Demand approaching available disk, memory, or configured cache limits. It can prevent a new view from warming even while existing readers continue working. |
| `tmpfs` / Btrfs | tmpfs stores filesystem data in memory; Btrfs is a disk filesystem used for the recorded local benchmark. The choice affects resource use and benchmark results. |

## HTTP and the CDN

| Term | Meaning |
| --- | --- |
| Auth subrequest / `/auth` | A separate request a CDN can make to an origin service to check access. It appeared in the original design; this integration does not add an `/auth` endpoint. |
| Cache-Control / revalidation | HTTP instructions for caching responses. `public, max-age=0, must-revalidate` allows storage but requires revalidation before reuse once stale. It does not bypass Pulp's guard check. |
| CDN / Akamai / edge / origin | A content delivery network caches responses near clients at edge servers. Akamai is the CDN discussed for hosted Pulp. The origin is the service contacted when the CDN needs content or authorization. |
| Conditional request / validator | A request that depends on whether content has changed. ETag and Last-Modified are validators the client can send back to avoid transferring unchanged bytes. |
| Content-Length / Content-Type / Content-Disposition | Headers describing response byte length, media type, and suggested presentation or download filename. Redirecting a listing to storage can change its presentation. |
| ETag / strong or weak ETag | An HTTP content validator. Indexed responses use the quoted artifact SHA-256 as a strong ETag. A weak ETag starts with `W/`. Do not assume an S3 object's own ETag is this SHA-256 value. |
| GET / HEAD / PUT | HTTP methods to retrieve a response body, retrieve headers without that body, or upload/replace a resource. HEAD requests can use indexed metadata without opening artifact storage. |
| HTTP / URL / MIME type | HTTP is the request/response protocol used by clients. A URL identifies the destination resource. A MIME type, such as `text/html`, tells clients how to interpret response bytes. |
| If-Match / If-Unmodified-Since | Preconditions requiring the resource to match a validator or not have changed after a date. A failed precondition can return 412. |
| If-None-Match / If-Modified-Since | Conditions asking for content only if it differs from a validator or is newer than a date. If-None-Match takes precedence when both are supplied. The same header name has a create-if-absent role for conditional S3 PUTs. |
| Last-Modified / membership date / file mtime | Last-Modified is an HTTP date. The index derives it from repository membership creation, not S3 modification time or the local cache file's mtime. Mtime is the filesystem's modification timestamp. |
| Middleware / guard ordering | Middleware runs around request handling. Guard ordering determines when authorization occurs relative to index lookups and conditional responses. Hosted patches must be checked against this sequence. |
| Range request / streaming | A range request asks for part of a file. Streaming sends bytes in chunks instead of loading the complete artifact into application memory. |
| Redirect / signed URL / presigned URL | A redirect tells a client to request another URL. A signed storage URL grants temporary access using a signature; its signed HTTP method must match the client's request. |
| SNAPSHOT | A Maven version name used for changing development content. Its paths can change; release-looking paths can also be replaced through the Pulp API, so indexed responses currently require revalidation for both. |

Status codes mentioned in the design, implementation, or tests:

| Code | Meaning in this context |
| --- | --- |
| 200 / 202 / 206 | A successful response, an accepted asynchronous task, or a successful partial-content response. |
| 301 / 302 | A redirect to a trailing-slash directory URL or, respectively in this adapter, a storage URL. |
| 304 | The requested content matches the client's validator; no response body is needed. |
| 403 / 404 | Access forbidden or resource not found. S3 can return 403 for an absent key when listing permission is missing, so these S3 errors do not prove an artifact path is absent. |
| 409 / 412 | A conflict or failed precondition. Conditional S3 writes use these responses to detect races or an already existing object. |
| 416 / 429 | An unsatisfiable byte range or a request that cannot proceed under the current limit. The immediate deploy API can return 429 when its reservation is busy. |

## Measurements and testing

| Term | Meaning |
| --- | --- |
| Benchmark / smoke measurement | A benchmark measures a defined workload. A smoke measurement is a small development run useful for catching problems, not proving production capacity. |
| Byte / bit / KiB / MiB / GiB | One byte is eight bits. KiB, MiB, and GiB are powers of 1,024 bytes. KB, MB, GB, and TB normally use powers of 1,000; the benchmark documentation distinguishes the units. |
| CPU / RAM / I/O | Processor work, main memory, and input/output such as disk or network operations. Improving one does not necessarily reduce the others. |
| DB / PostgreSQL / SQL / ORM / Django | DB means database; PostgreSQL is Pulp's database. SQL is its query language. Django's object-relational mapper, or ORM, turns model operations into SQL. |
| End-to-end / engine-only / core-only | End-to-end measures the complete client operation. Engine-only measures index operations; core-only isolates pulpcore work from Maven hooks. |
| Fixture / randomized history | A fixture supplies controlled test data or services. Randomized-history tests generate changes and compare the resulting index against a simpler expected model. |
| HTML / XML / JSON / JSON Lines | Text formats used here for web pages, Maven metadata, and structured index manifests. JSON Lines puts one JSON value on each line, as in the standalone benchmark output. |
| Latency / throughput / burst | Latency is time for an operation. Throughput is operations completed per time interval. A burst is many arrivals together; a low daily average does not rule out a large queue during a burst. |
| Median / p50 / p95 / p99 | Percentiles of measured times. The median is p50. P99 is a time at or below which roughly 99% of observations fall; it is not the worst observed time. |
| Migration / schema / OpenAPI | A migration changes the database schema, its tables and rules. OpenAPI describes HTTP endpoints and their request/response structures; it is a separate kind of schema. |
| O(N) / O(log N) | Descriptions of how work grows with input size N. A full scan or base rewrite grows roughly with N; binary search grows with log N. These are growth rates, not measured response times. |
| RSS / peak RSS | Resident set size: memory resident for a process at a point in time. Peak RSS is its highest observed value. Mapped file pages count, so it is not a measurement of private Python heap alone. |
| Sequential / concurrent / representative workload | Sequential operations run one after another; concurrent ones overlap. A representative workload resembles actual repository sizes, changes, request patterns, and deployment conditions. |
| Unit / lifecycle / functional / wire tests | Unit tests exercise individual behavior. Lifecycle tests here use real repository versions and PostgreSQL. Functional tests exercise deployed Pulp operations. S3 wire tests make real service requests instead of using a test double. |
| WAL / write amplification | WAL is PostgreSQL's write-ahead log used for recovery. Write amplification is extra data written while applying a change, such as sort runs, merged segments, manifests, and database logging. Engine byte counters do not measure all of it. |

Names such as `CacheFull`, `InvalidIndex`, `IndexUnavailable`, `NeedsCompaction`,
and `PublicationConflict` are implementation exceptions. Respectively, they signal
insufficient cache capacity, invalid index data, unavailable index storage/state,
a need to compact before another update, and conflicting immutable publication.
None is an authoritative "artifact not found" result.
