from pulpcore.plugin.viewsets import (
    ContentFilter,
    DistributionViewSet,
    RemoteViewSet,
    RepositoryVersionViewSet,
    RepositoryViewSet,
    SingleArtifactContentUploadViewSet,
)
from pulpcore.plugin.actions import ModifyRepositoryActionMixin


from pulp_maven.app.models import MavenArtifact, MavenRemote, MavenRepository, MavenDistribution

from pulp_maven.app.serializers import (
    MavenArtifactSerializer,
    MavenRemoteSerializer,
    MavenRepositorySerializer,
    MavenDistributionSerializer,
)


class MavenArtifactFilter(ContentFilter):
    """
    FilterSet for MavenArtifact.
    """

    class Meta:
        model = MavenArtifact
        fields = ["group_id", "artifact_id", "version", "filename"]


class MavenArtifactViewSet(SingleArtifactContentUploadViewSet):
    """
    A ViewSet for MavenArtifact.
    """

    endpoint_name = "artifact"
    queryset = MavenArtifact.objects.prefetch_related("_artifacts").all()
    serializer_class = MavenArtifactSerializer
    filterset_class = MavenArtifactFilter


class MavenRemoteViewSet(RemoteViewSet):
    """
    A ViewSet for MavenRemote.
    """

    endpoint_name = "maven"
    queryset = MavenRemote.objects.all()
    serializer_class = MavenRemoteSerializer


class MavenRepositoryViewSet(RepositoryViewSet, ModifyRepositoryActionMixin):
    """
    A ViewSet for MavenRemote.
    """

    endpoint_name = "maven"
    queryset = MavenRepository.objects.all()
    serializer_class = MavenRepositorySerializer


class MavenRepositoryVersionViewSet(RepositoryVersionViewSet):
    """
    MavenRepositoryVersion represents a single Maven repository version.
    """

    parent_viewset = MavenRepositoryViewSet


class MavenDistributionViewSet(DistributionViewSet):
    """
    ViewSet for Maven Distributions.
    """

    endpoint_name = "maven"
    queryset = MavenDistribution.objects.all()
    serializer_class = MavenDistributionSerializer
