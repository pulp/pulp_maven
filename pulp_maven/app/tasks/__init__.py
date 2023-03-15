import datetime

from pulp_maven.app.models import MavenRemote, MavenRepository

from pulpcore.plugin.models import Content, ContentArtifact, RemoteArtifact


def add_cached_content_to_repository(repository_pk=None, remote_pk=None):
    """
    Create a new repository version by adding content that was cached by pulpcore-content when
    streaming it from a remote.

    Args:
        repository_pk (uuid): The primary key for a Repository for which a new Repository Version
            should be created.
        remote_pk (uuid): The primary key for a Remote which will be used to identify Content
            created by pulpcore-content when it streamed it to clients.
    """
    repository = MavenRepository.objects.get(pk=repository_pk)
    remote = MavenRemote.objects.get(pk=remote_pk)

    latest_version = repository.latest_version()

    if latest_version.number == 0:
        date_min = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    else:
        date_min = latest_version.pulp_created
    with repository.new_version(base_version=None) as new_version:
        ca_id_list = RemoteArtifact.objects.filter(
            remote=remote, pulp_created__gte=date_min
        ).values_list("content_artifact")
        content_list = ContentArtifact.objects.filter(pk__in=ca_id_list).values_list("content")
        new_version.add_content(Content.objects.filter(pk__in=content_list))
