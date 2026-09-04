"""Rebuild-suffix helpers and catalog query parsing (no Django models).

CI unit tests use ``pytest -p no:pulpcore``. Import this module, not ``catalog``:
``catalog`` pulls in Django models and fails collection with AppRegistryNotReady.
"""

import re

# Lightwell-style rebuild: 5.3.18.rhlw-00003 -> 5.3.18. Not hard-coded to "rhlw".
BUILD_SUFFIX_RE = re.compile(r"\.[a-zA-Z]+-\d+$")

PACKAGE_INDEX_ORDERING_FIELDS = frozenset({"group_id", "artifact_id", "last_updated"})
DEFAULT_PACKAGE_INDEX_ORDERING = ("group_id", "artifact_id")


def strip_build_suffix(version):
    """Return ``version`` with a trailing rebuild suffix removed, else unchanged."""
    if not version:
        return version
    return BUILD_SUFFIX_RE.sub("", version)


def rebuild_release(version):
    """Return the rebuild qualifier without the leading dot, or an empty string."""
    if not version:
        return ""
    base = strip_build_suffix(version)
    if version == base:
        return ""
    if version.startswith(base + "."):
        return version[len(base) + 1 :]
    return ""


def version_sort_key(version):
    """Order Maven-like versions with numeric tokens compared as integers."""
    if not version:
        return ()
    key = []
    for token in re.split(r"([.-])", version):
        if token.isdigit():
            key.append((0, int(token)))
        else:
            key.append((1, token))
    return tuple(key)


def parse_package_search(search):
    """Parse the catalog ``search`` query value.

    Returns ``None`` when the parameter should not filter. Otherwise:

    * ``("or", term)`` — no ``:``; ``group_id`` or ``artifact_id`` contains ``term``
    * ``("and", group_term, artifact_term)`` — split on ``:``. Empty terms mean that
      side is unconstrained. A third segment (Maven version) is ignored.

    ``:`` and whitespace-only values are a no-op. Parts are stripped.
    """
    if search is None:
        return None
    term = str(search).strip()
    if not term or term == ":":
        return None
    if ":" not in term:
        return ("or", term)
    parts = term.split(":", 2)
    group_term = parts[0].strip()
    artifact_term = parts[1].strip() if len(parts) > 1 else ""
    if not group_term and not artifact_term:
        return None
    return ("and", group_term, artifact_term)


def normalize_package_index_ordering(raw_values):
    """Turn ``ordering`` query values into ``order_by`` arguments.

    ``group_id`` without ``artifact_id`` implies the same-direction ``artifact_id``
    so package-name sort is ``ORDER BY group_id, artifact_id``. Unknown fields
    raise ``ValueError``.
    """
    fields = []
    for item in raw_values:
        if not item:
            continue
        for part in str(item).split(","):
            part = part.strip()
            if part:
                fields.append(part)

    if not fields:
        return list(DEFAULT_PACKAGE_INDEX_ORDERING)

    normalized = []
    seen = set()
    for field in fields:
        descending = field.startswith("-")
        name = field[1:] if descending else field
        if name not in PACKAGE_INDEX_ORDERING_FIELDS:
            raise ValueError(f"Unknown ordering field: '{name}'.")
        if name in seen:
            continue
        seen.add(name)
        normalized.append(f"-{name}" if descending else name)

    names = [term[1:] if term.startswith("-") else term for term in normalized]
    if "group_id" in names and "artifact_id" not in names:
        group_term = next(term for term in normalized if term.lstrip("-") == "group_id")
        artifact_term = "-artifact_id" if group_term.startswith("-") else "artifact_id"
        normalized.insert(normalized.index(group_term) + 1, artifact_term)

    have = {term.lstrip("-") for term in normalized}
    if "group_id" not in have:
        normalized.append("group_id")
    if "artifact_id" not in have:
        normalized.append("artifact_id")
    return normalized
