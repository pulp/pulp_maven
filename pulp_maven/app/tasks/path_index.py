"""Explicit maintenance, always dispatched with a repository reservation."""

from pulpcore.plugin.models import RepositoryVersion
from pulpcore.plugin.util import set_domain

from pulp_maven.app.models import MavenRepository
from pulp_maven.app.path_index.config import enabled, profile, store
from pulp_maven.app.path_index.publish import baseline_entries
from pulp_maven.app.path_index.state import INFO_KEY, attach, read


def selected_version(repository_pk, version_pk=None):
    repository = MavenRepository.objects.select_related("pulp_domain").get(pk=repository_pk)
    set_domain(repository.pulp_domain)
    if not enabled(repository):
        raise ValueError("Enable path_index on the repository and configure the experiment first")
    query = RepositoryVersion.objects.filter(repository=repository, complete=True).only(
        "pk", "info"
    )
    version = query.get(pk=version_pk) if version_pk else query.order_by("-number").first()
    if version is None:
        raise ValueError("The repository has no complete version")
    return repository, version


def build_path_index(repository_pk, version_pk=None):
    """Build a retained version without changing its content or creating a version."""
    repository, version = selected_version(repository_pk, version_pk)
    index = store(repository)
    value = version.info.get(INFO_KEY)
    if isinstance(value, dict) and value.get("profile") == profile(repository.pulp_domain):
        read(index, version, repository)  # Idempotent, but never conceal corrupt storage.
        return str(version.pk)
    manifest = index.create(str(version.pk), baseline_entries(repository, version))
    attach(version, repository, manifest)
    version.save(update_fields=["info"])
    return str(version.pk)


def compact_path_index(repository_pk):
    """Rebase the latest completed version; advertise only after the S3 PUT succeeds."""
    repository, version = selected_version(repository_pk)
    index = store(repository)
    manifest = index.compact(read(index, version, repository), rebase=True)
    attach(version, repository, manifest, checkpoint=True)
    version.save(update_fields=["info"])
    return str(version.pk)
