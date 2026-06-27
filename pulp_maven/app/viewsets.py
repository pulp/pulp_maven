from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from pulpcore.plugin.actions import ModifyRepositoryActionMixin
from pulpcore.plugin.serializers import AsyncOperationResponseSerializer
from pulpcore.plugin.tasking import dispatch
from pulpcore.plugin.viewsets import (
    ContentFilter,
    DistributionViewSet,
    OperationPostponedResponse,
    RemoteViewSet,
    RepositoryVersionViewSet,
    RepositoryViewSet,
    SingleArtifactContentUploadViewSet,
)

from pulp_maven.app.models import (
    MavenArtifact,
    MavenDistribution,
    MavenMetadata,
    MavenRemote,
    MavenRepository,
)
from pulp_maven.app.serializers import (
    MavenArtifactSerializer,
    MavenArtifactUploadSerializer,
    MavenDistributionSerializer,
    MavenMetadataSerializer,
    MavenMetadataUploadSerializer,
    MavenRemoteSerializer,
    MavenRepositorySerializer,
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


class MavenArtifactViewSet(SingleArtifactContentUploadViewSet):
    """
    A ViewSet for MavenArtifact.
    """

    endpoint_name = "artifact"
    queryset = MavenArtifact.objects.all()
    serializer_class = MavenArtifactSerializer
    filterset_class = MavenArtifactFilter

    @extend_schema(
        description="Synchronously upload a Maven artifact.",
        request=MavenArtifactUploadSerializer,
        responses={201: MavenArtifactSerializer},
        summary="Upload a Maven artifact synchronously.",
    )
    @action(detail=False, methods=["post"], serializer_class=MavenArtifactUploadSerializer)
    def upload(self, request):
        """Create a Maven artifact synchronously."""
        serializer = self.get_serializer(data=request.data)
        with transaction.atomic():
            serializer.is_valid(raise_exception=True)
            serializer.save()

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class MavenMetadataFilter(ContentFilter):
    """
    FilterSet for MavenMetadata.
    """

    class Meta:
        model = MavenMetadata
        fields = ["group_id", "artifact_id", "version", "filename"]


class MavenMetadataViewSet(SingleArtifactContentUploadViewSet):
    """
    A ViewSet for MavenMetadata.
    """

    endpoint_name = "metadata"
    queryset = MavenMetadata.objects.all()
    serializer_class = MavenMetadataSerializer
    filterset_class = MavenMetadataFilter

    @extend_schema(
        description="Synchronously upload a Maven metadata file.",
        request=MavenMetadataUploadSerializer,
        responses={201: MavenMetadataSerializer},
        summary="Upload a Maven metadata file synchronously.",
    )
    @action(detail=False, methods=["post"], serializer_class=MavenMetadataUploadSerializer)
    def upload(self, request):
        """Create a Maven metadata content unit synchronously."""
        serializer = self.get_serializer(data=request.data)
        with transaction.atomic():
            serializer.is_valid(raise_exception=True)
            serializer.save()

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


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
