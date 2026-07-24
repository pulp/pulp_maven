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
    ReadOnlyContentViewSet,
    RemoteViewSet,
    RepositoryVersionViewSet,
    RepositoryViewSet,
    RolesMixin,
    SingleArtifactContentUploadViewSet,
)

from pulp_maven.app.models import (
    MavenArtifact,
    MavenDistribution,
    MavenMetadata,
    MavenPackage,
    MavenRemote,
    MavenRepository,
)
from pulp_maven.app.serializers import (
    MavenArtifactSerializer,
    MavenArtifactUploadSerializer,
    MavenDistributionSerializer,
    MavenMetadataSerializer,
    MavenMetadataUploadSerializer,
    MavenPackageSerializer,
    MavenRemoteSerializer,
    MavenRepositorySerializer,
    RepositoryAddCachedContentSerializer,
)
from pulp_maven.app.tasks import add_cached_content_to_repository, repair_metadata


class MavenArtifactFilter(ContentFilter):
    """
    FilterSet for MavenArtifact.
    """

    class Meta:
        model = MavenArtifact
        fields = ["group_id", "artifact_id", "version", "filename"]  # noqa: RUF012


class MavenArtifactViewSet(SingleArtifactContentUploadViewSet):
    """
    A ViewSet for MavenArtifact.
    """

    endpoint_name = "artifact"
    queryset = MavenArtifact.objects.all()
    serializer_class = MavenArtifactSerializer
    filterset_class = MavenArtifactFilter

    DEFAULT_ACCESS_POLICY = {  # noqa: RUF012
        "statements": [
            {
                "action": ["list", "retrieve"],
                "principal": "authenticated",
                "effect": "allow",
            },
            {
                "action": ["create"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_required_repo_perms_on_upload:maven.modify_mavenrepository",
                    "has_required_repo_perms_on_upload:maven.view_mavenrepository",
                    "has_upload_param_model_or_domain_or_obj_perms:core.change_upload",
                ],
            },
            {
                "action": ["upload"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_perms:maven.add_mavenartifact",
                ],
            },
            {
                "action": ["set_label", "unset_label"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_perms:core.manage_content_labels",
                ],
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }

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
        fields = ["group_id", "artifact_id", "version", "filename"]  # noqa: RUF012


class MavenMetadataViewSet(SingleArtifactContentUploadViewSet):
    """
    A ViewSet for MavenMetadata.
    """

    endpoint_name = "metadata"
    queryset = MavenMetadata.objects.all()
    serializer_class = MavenMetadataSerializer
    filterset_class = MavenMetadataFilter

    DEFAULT_ACCESS_POLICY = {  # noqa: RUF012
        "statements": [
            {
                "action": ["list", "retrieve"],
                "principal": "authenticated",
                "effect": "allow",
            },
            {
                "action": ["create"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_required_repo_perms_on_upload:maven.modify_mavenrepository",
                    "has_required_repo_perms_on_upload:maven.view_mavenrepository",
                    "has_upload_param_model_or_domain_or_obj_perms:core.change_upload",
                ],
            },
            {
                "action": ["upload"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_perms:maven.add_mavenmetadata",
                ],
            },
            {
                "action": ["set_label", "unset_label"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_perms:core.manage_content_labels",
                ],
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }

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


class MavenPackageFilter(ContentFilter):
    """
    FilterSet for MavenPackage.
    """

    class Meta:
        model = MavenPackage
        fields = ["group_id", "artifact_id", "version", "name", "packaging"]  # noqa: RUF012


class MavenPackageViewSet(ReadOnlyContentViewSet):
    """
    A read-only ViewSet for MavenPackage.

    MavenPackage represents a logical Maven package at the GAV (groupId,
    artifactId, version) level. Packages are automatically created when
    artifacts are added to a repository.
    """

    endpoint_name = "package"
    queryset = MavenPackage.objects.all()
    serializer_class = MavenPackageSerializer
    filterset_class = MavenPackageFilter

    DEFAULT_ACCESS_POLICY = {  # noqa: RUF012
        "statements": [
            {
                "action": ["list", "retrieve"],
                "principal": "authenticated",
                "effect": "allow",
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }


class MavenRemoteViewSet(RemoteViewSet, RolesMixin):
    """
    A ViewSet for MavenRemote.
    """

    endpoint_name = "maven"
    queryset = MavenRemote.objects.all()
    serializer_class = MavenRemoteSerializer

    queryset_filtering_required_permission = "maven.view_mavenremote"

    DEFAULT_ACCESS_POLICY = {  # noqa: RUF012
        "statements": [
            {
                "action": ["list", "my_permissions"],
                "principal": "authenticated",
                "effect": "allow",
            },
            {
                "action": ["create"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_perms:maven.add_mavenremote",
            },
            {
                "action": ["retrieve"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:maven.view_mavenremote",
            },
            {
                "action": ["update", "partial_update"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.change_mavenremote",
                    "has_model_or_domain_or_obj_perms:maven.view_mavenremote",
                ],
            },
            {
                "action": ["set_label", "unset_label"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.change_mavenremote",
                    "has_model_or_domain_or_obj_perms:maven.view_mavenremote",
                ],
            },
            {
                "action": ["destroy"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.delete_mavenremote",
                    "has_model_or_domain_or_obj_perms:maven.view_mavenremote",
                ],
            },
            {
                "action": ["list_roles", "add_role", "remove_role"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": ["has_model_or_domain_or_obj_perms:maven.manage_roles_mavenremote"],
            },
        ],
        "creation_hooks": [
            {
                "function": "add_roles_for_object_creator",
                "parameters": {"roles": "maven.mavenremote_owner"},
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }
    LOCKED_ROLES = {  # noqa: RUF012
        "maven.mavenremote_creator": ["maven.add_mavenremote"],
        "maven.mavenremote_owner": [
            "maven.view_mavenremote",
            "maven.change_mavenremote",
            "maven.delete_mavenremote",
            "maven.manage_roles_mavenremote",
        ],
        "maven.mavenremote_viewer": ["maven.view_mavenremote"],
    }


class MavenRepositoryViewSet(RepositoryViewSet, ModifyRepositoryActionMixin, RolesMixin):
    """
    A ViewSet for MavenRepository.
    """

    endpoint_name = "maven"
    queryset = MavenRepository.objects.all()
    serializer_class = MavenRepositorySerializer

    queryset_filtering_required_permission = "maven.view_mavenrepository"

    DEFAULT_ACCESS_POLICY = {  # noqa: RUF012
        "statements": [
            {
                "action": ["list", "my_permissions"],
                "principal": "authenticated",
                "effect": "allow",
            },
            {
                "action": ["create"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_perms:maven.add_mavenrepository",
                    "has_remote_param_model_or_domain_or_obj_perms:maven.view_mavenremote",
                ],
            },
            {
                "action": ["retrieve"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:maven.view_mavenrepository",
            },
            {
                "action": ["update", "partial_update"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.change_mavenrepository",
                    "has_model_or_domain_or_obj_perms:maven.view_mavenrepository",
                    "has_remote_param_model_or_domain_or_obj_perms:maven.view_mavenremote",
                ],
            },
            {
                "action": ["set_label", "unset_label"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.change_mavenrepository",
                    "has_model_or_domain_or_obj_perms:maven.view_mavenrepository",
                ],
            },
            {
                "action": ["destroy"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.delete_mavenrepository",
                    "has_model_or_domain_or_obj_perms:maven.view_mavenrepository",
                ],
            },
            {
                "action": ["add_cached_content"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.modify_mavenrepository",
                    "has_remote_param_model_or_domain_or_obj_perms:maven.view_mavenremote",
                    "has_model_or_domain_or_obj_perms:maven.view_mavenrepository",
                ],
            },
            {
                "action": ["repair_metadata"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.repair_mavenrepository",
                    "has_model_or_domain_or_obj_perms:maven.view_mavenrepository",
                ],
            },
            {
                "action": ["modify"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.modify_mavenrepository",
                    "has_model_or_domain_or_obj_perms:maven.view_mavenrepository",
                ],
            },
            {
                "action": ["list_roles", "add_role", "remove_role"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.manage_roles_mavenrepository"
                ],
            },
        ],
        "creation_hooks": [
            {
                "function": "add_roles_for_object_creator",
                "parameters": {"roles": "maven.mavenrepository_owner"},
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }
    LOCKED_ROLES = {  # noqa: RUF012
        "maven.mavenrepository_creator": ["maven.add_mavenrepository"],
        "maven.mavenrepository_owner": [
            "maven.view_mavenrepository",
            "maven.change_mavenrepository",
            "maven.delete_mavenrepository",
            "maven.modify_mavenrepository",
            "maven.repair_mavenrepository",
            "maven.manage_roles_mavenrepository",
        ],
        "maven.mavenrepository_viewer": ["maven.view_mavenrepository"],
    }

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

    @extend_schema(
        description=(
            "Trigger an asynchronous task to regenerate all maven-metadata.xml files "
            "and their checksums for every artifact in the repository."
        ),
        summary="Repair metadata",
        request=None,
        responses={202: AsyncOperationResponseSerializer},
    )
    @action(detail=True, methods=["post"])
    def repair_metadata(self, request, pk):
        """
        Regenerate maven-metadata.xml for all (group_id, artifact_id) pairs.
        """
        repository = self.get_object()
        result = dispatch(
            repair_metadata,
            exclusive_resources=[repository],
            kwargs={"repository_pk": str(repository.pk)},
        )
        return OperationPostponedResponse(result, request)


class MavenRepositoryVersionViewSet(RepositoryVersionViewSet):
    """
    MavenRepositoryVersion represents a single Maven repository version.
    """

    parent_viewset = MavenRepositoryViewSet

    DEFAULT_ACCESS_POLICY = {  # noqa: RUF012
        "statements": [
            {
                "action": ["list", "retrieve"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_repository_model_or_domain_or_obj_perms:maven.view_mavenrepository",
            },
            {
                "action": ["destroy"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_repository_model_or_domain_or_obj_perms:maven.delete_mavenrepository",
                    "has_repository_model_or_domain_or_obj_perms:maven.view_mavenrepository",
                ],
            },
            {
                "action": ["repair"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_repository_model_or_domain_or_obj_perms:maven.repair_mavenrepository",
                    "has_repository_model_or_domain_or_obj_perms:maven.view_mavenrepository",
                ],
            },
        ],
    }


class MavenDistributionViewSet(DistributionViewSet, RolesMixin):
    """
    ViewSet for Maven Distributions.
    """

    endpoint_name = "maven"
    queryset = MavenDistribution.objects.all()
    serializer_class = MavenDistributionSerializer

    queryset_filtering_required_permission = "maven.view_mavendistribution"

    DEFAULT_ACCESS_POLICY = {  # noqa: RUF012
        "statements": [
            {
                "action": ["list", "my_permissions"],
                "principal": "authenticated",
                "effect": "allow",
            },
            {
                "action": ["create"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_perms:maven.add_mavendistribution",
                    "has_repo_or_repo_ver_param_model_or_domain_or_obj_perms:maven.view_mavenrepository",
                ],
            },
            {
                "action": ["retrieve"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": "has_model_or_domain_or_obj_perms:maven.view_mavendistribution",
            },
            {
                "action": ["update", "partial_update"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.change_mavendistribution",
                    "has_model_or_domain_or_obj_perms:maven.view_mavendistribution",
                    "has_repo_or_repo_ver_param_model_or_domain_or_obj_perms:maven.view_mavenrepository",
                ],
            },
            {
                "action": ["set_label", "unset_label"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.change_mavendistribution",
                    "has_model_or_domain_or_obj_perms:maven.view_mavendistribution",
                ],
            },
            {
                "action": ["destroy"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.delete_mavendistribution",
                    "has_model_or_domain_or_obj_perms:maven.view_mavendistribution",
                ],
            },
            {
                "action": ["list_roles", "add_role", "remove_role"],
                "principal": "authenticated",
                "effect": "allow",
                "condition": [
                    "has_model_or_domain_or_obj_perms:maven.manage_roles_mavendistribution"
                ],
            },
        ],
        "creation_hooks": [
            {
                "function": "add_roles_for_object_creator",
                "parameters": {"roles": "maven.mavendistribution_owner"},
            },
        ],
        "queryset_scoping": {"function": "scope_queryset"},
    }
    LOCKED_ROLES = {  # noqa: RUF012
        "maven.mavendistribution_creator": ["maven.add_mavendistribution"],
        "maven.mavendistribution_owner": [
            "maven.view_mavendistribution",
            "maven.change_mavendistribution",
            "maven.delete_mavendistribution",
            "maven.manage_roles_mavendistribution",
        ],
        "maven.mavendistribution_viewer": ["maven.view_mavendistribution"],
    }
