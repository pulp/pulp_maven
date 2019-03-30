from pulpcore.plugin import viewsets as core

from . import models, serializers


class MavenArtifactFilter(core.ContentFilter):
    """
    FilterSet for MavenArtifact.
    """

    class Meta:
        model = models.MavenArtifact
        fields = [
            'group_id', 'artifact_id', 'version', 'filename'
        ]


class MavenArtifactViewSet(core.ContentViewSet):
    """
    A ViewSet for MavenArtifact.
    """

    endpoint_name = 'artifact'
    queryset = models.MavenArtifact.objects.all()
    serializer_class = serializers.MavenArtifactSerializer
    filterset_class = MavenArtifactFilter


class MavenRemoteViewSet(core.RemoteViewSet):
    """
    A ViewSet for MavenRemote.
    """

    endpoint_name = 'maven'
    queryset = models.MavenRemote.objects.all()
    serializer_class = serializers.MavenRemoteSerializer
