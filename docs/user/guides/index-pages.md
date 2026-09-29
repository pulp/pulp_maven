# HTML Directory Index Pages

Pulp Maven pre-generates HTML directory index pages when a repository version is
finalized. Clients browsing a Maven repository at a directory URL (e.g.
`/pulp/maven/<base_path>/com/example/mylib/`) receive a pre-built listing page
served directly from storage, avoiding the more expensive on-demand query for
large repositories.

## How It Works

When `finalize_new_version` runs after content is added or removed, Pulp Maven
generates an `index.html` ContentArtifact at every ancestor directory path
touched by the change. The pulpcore content handler serves the pre-generated
page when it finds an `index.html` entry at the requested path; directories
unaffected by the version change continue to serve their existing pages.

Each page includes the name, size, and last-modified date of each direct child
entry (files and subdirectories), matching the information produced by the
on-demand fallback.

## Content Type

The generated pages are stored as `MavenIndexPage` content units (type
`maven.index-page`). They are visible through the standard Pulp content API
and are deduplication-keyed on `(path, sha256)` so identical pages are reused
across repository versions.


## Incremental page generation

Set the repository label `incremental_index_pages` to the string `"true"` to
maintain a derived table of each directory's immediate children. This is independent
of the experimental binary path index and does not require S3 index storage.

The first subsequent version builds the summary from its content. Later versions
read only changed content paths, render affected pages, and reuse unchanged HTML
artifacts and memberships. A change to a file normally updates its containing
page. Creating or removing a directory also changes its parent's page. Directory
links omit aggregate sizes and dates so a file update does not invalidate every
ancestor. File links retain their size and repository membership date.

Pages remain ordinary immutable `MavenIndexPage` content. Crawlers use the existing
content handler, and distributions pinned to older versions keep their old pages.
Uploads may be created as orphans and added in one repository `modify` call; all
changes in that version share one page-generation pass.

The summary is expendable. A failed version, a gap after disabling the option, or
`repair_index_pages` causes a full rebuild under the repository reservation. A
summary cursor records the version it describes; only the previous completed
version can be used for an incremental update. No-op versions preserve the cursor.

Remove the label to return to the existing generator. Existing summary rows are
retained for recovery and deleted when the repository is deleted. The first build
and a very large single directory can still be expensive. Maven metadata generation
and pulpcore version membership/counting costs are unchanged.
