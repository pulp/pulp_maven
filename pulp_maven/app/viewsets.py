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
    ContentViewSet,
    DistributionViewSet,
    OperationPostponedResponse,
    RemoteViewSet,
    RepositoryVersionViewSet,
    RepositoryViewSet,
)

from pulp_maven.app.maven_deploy_api import has_task_completed
from pulp_maven.app.models import MavenArtifact, MavenDistribution, MavenRemote, MavenRepository
from pulp_maven.app.serializers import (
    MavenArtifactSerializer,
    MavenDistributionSerializer,
    MavenRemoteSerializer,
    MavenRepositorySerializer,
    RepositoryAddCachedContentSerializer,
)
from pulp_maven.app.tasks import aadd_and_remove, add_cached_content_to_repository


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

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        repository = serializer.validated_data.pop("repository", None)
        with transaction.atomic():
            serializer.save()

        if repository:
            dispatched_task = dispatch(
                aadd_and_remove,
                exclusive_resources=[repository],
                immediate=True,
                deferred=False,
                kwargs={
                    "repository_pk": str(repository.pk),
                    "add_content_units": [str(serializer.instance.pk)],
                    "remove_content_units": [],
                },
            )
            has_task_completed(dispatched_task)

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
