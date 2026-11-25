Sped up creating repository versions in large repositories. Duplicate removal skips content types
whose uniqueness already rules out duplicates, and package, maven-metadata.xml and prefixes.txt
updates run a fixed number of set-based queries instead of one or more per affected artifact.
