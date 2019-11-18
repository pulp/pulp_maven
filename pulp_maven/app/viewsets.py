from pulpcore.plugin import viewsets as core

from . import models, serializers


class MavenArtifactFilter(core.ContentFilter):
    """
    FilterSet for MavenArtifact.
    """

    class Meta:
        model = models.MavenArtifact
        fields = ["group_id", "artifact_id", "version", "filename"]


class MavenArtifactViewSet(core.ContentViewSet):
    """
    A ViewSet for MavenArtifact.
    """

    endpoint_name = "artifact"
    queryset = models.MavenArtifact.objects.all()
    serializer_class = serializers.MavenArtifactSerializer
    filterset_class = MavenArtifactFilter


class MavenRemoteViewSet(core.RemoteViewSet):
    """
    A ViewSet for MavenRemote.
    """

    endpoint_name = "maven"
    queryset = models.MavenRemote.objects.all()
    serializer_class = serializers.MavenRemoteSerializer


class MavenRepositoryViewSet(core.RepositoryViewSet):
    """
    A ViewSet for MavenRemote.
    """

    endpoint_name = "maven"
    queryset = models.MavenRepository.objects.all()
    serializer_class = serializers.MavenRepositorySerializer


class MavenRepositoryVersionViewSet(core.RepositoryVersionViewSet):
    """
    MavenRepositoryVersion represents a single Maven repository version.
    """

    parent_viewset = MavenRepositoryViewSet


class MavenDistributionViewSet(core.BaseDistributionViewSet):
    """
    ViewSet for Maven Distributions.
    """

    endpoint_name = "maven"
    queryset = models.MavenDistribution.objects.all()
    serializer_class = serializers.MavenDistributionSerializer
