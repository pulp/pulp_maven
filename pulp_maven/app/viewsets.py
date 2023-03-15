from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from pulpcore.plugin.serializers import AsyncOperationResponseSerializer

from pulpcore.plugin.tasking import dispatch

from pulpcore.plugin.viewsets import (
    ContentFilter,
    ContentViewSet,
    DistributionViewSet,
    OperationPostponedResponse,
    RemoteViewSet,
    RepositoryVersionViewSet,
    RepositoryViewSet,
)

from pulp_maven.app.models import MavenArtifact, MavenRemote, MavenRepository, MavenDistribution

from pulp_maven.app.serializers import (
    MavenArtifactSerializer,
    MavenRemoteSerializer,
    MavenRepositorySerializer,
    MavenDistributionSerializer,
    RepositoryAddCachedContentSerializer,
)

from pulp_maven.app.tasks import add_cached_content_to_repository


class MavenArtifactFilter(ContentFilter):
    """
    FilterSet for MavenArtifact.
    """

    class Meta:
        model = MavenArtifact
        fields = ["group_id", "artifact_id", "version", "filename"]


class MavenArtifactViewSet(ContentViewSet):
    """
    A ViewSet for MavenArtifact.
    """

    endpoint_name = "artifact"
    queryset = MavenArtifact.objects.all()
    serializer_class = MavenArtifactSerializer
    filterset_class = MavenArtifactFilter


class MavenRemoteViewSet(RemoteViewSet):
    """
    A ViewSet for MavenRemote.
    """

    endpoint_name = "maven"
    queryset = MavenRemote.objects.all()
    serializer_class = MavenRemoteSerializer


class MavenRepositoryViewSet(RepositoryViewSet):
    """
    A ViewSet for MavenRemote.
    """

    endpoint_name = "maven"
    queryset = MavenRepository.objects.all()
    serializer_class = MavenRepositorySerializer

    @extend_schema(
        description="Trigger an asynchronous task to add cached content to a repository.",
        summary="Add cached content",
        responses={202: AsyncOperationResponseSerializer},
    )
    @action(detail=True, methods=["post"], serializer_class=RepositoryAddCachedContentSerializer)
    def add_cached_content(self, request, pk):
        """
        Add to the repository any MavenArtifact and MavenMetadata that was cached using the
        remote since the last repository version was created.

        The ``repository`` field has to be provided.
        """
        serializer = RepositoryAddCachedContentSerializer(
            data=request.data, context={"request": request, "repository_pk": pk}
        )
        serializer.is_valid(raise_exception=True)

        repository = self.get_object()
        remote = serializer.validated_data.get("remote", repository.remote)

        result = dispatch(
            add_cached_content_to_repository,
            shared_resources=[remote],
            exclusive_resources=[repository],
            kwargs={
                "remote_pk": str(remote.pk),
                "repository_pk": str(repository.pk),
            },
        )
        return OperationPostponedResponse(result, request)


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
