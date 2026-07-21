Added MavenPackage content type representing a logical Maven package at the
GAV (groupId, artifactId, version) level. Packages are automatically created
when artifacts are added to a repository and provide a searchable, read-only
API endpoint at /pulp/api/v3/content/maven/package/.
