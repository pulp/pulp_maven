Sped up creating repository versions in large repositories. Duplicate removal skips content types
whose uniqueness already rules out duplicates and runs one query per content type instead of
filtering the whole version once per thousand added units.
