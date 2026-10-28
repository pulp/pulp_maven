Finalizing a repository version is cheaper: packages and metadata are added in one step instead of
rewriting the version's content ids for each, `prefixes.txt` no longer scans every artifact, index
pages are looked up by membership, and generated files are written to storage in parallel.
