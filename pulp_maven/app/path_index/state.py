"""Small, committed manifest descriptors stored in RepositoryVersion.info."""

import hashlib

from .config import profile
from .format import InvalidIndex
from .store import DIGEST

INFO_KEY = "maven_path_index"


def descriptor(version, repository):
    value = version.info.get(INFO_KEY)
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or value.get("format") != 1
        or type(value.get("checkpoint")) is not bool
        or not isinstance(value.get("digest"), str)
        or not DIGEST.fullmatch(value["digest"])
        or value.get("profile") != profile(repository.pulp_domain)
    ):
        raise InvalidIndex("Invalid or incompatible version index descriptor")
    return value


def attach(version, repository, manifest, *, checkpoint=False):
    """The caller saves info with completion, or under a maintenance reservation."""
    version.info = {
        **version.info,
        INFO_KEY: {
            "format": 1,
            "profile": profile(repository.pulp_domain),
            "digest": hashlib.sha256(manifest.encode()).hexdigest(),
            "checkpoint": checkpoint,
        },
    }


def read(index, version, repository):
    value = descriptor(version, repository)
    if value is None:
        raise InvalidIndex("The version has no published index")
    manifest = (
        index.read_checkpoint(value["digest"])
        if value["checkpoint"]
        else index.read_version(str(version.pk))
    )
    if (
        manifest.version_id != str(version.pk)
        or hashlib.sha256(manifest.encode()).hexdigest() != value["digest"]
    ):
        raise InvalidIndex("The S3 manifest differs from the completed version descriptor")
    return manifest
