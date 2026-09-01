"""Rebuild-suffix helpers for Maven version strings."""

import re

# Lightwell-style rebuild: 5.3.18.rhlw-00003 -> 5.3.18. Not hard-coded to "rhlw".
BUILD_SUFFIX_RE = re.compile(r"\.[a-zA-Z]+-\d+$")


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
