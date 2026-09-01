from django.db import transaction
from django_filters import CharFilter
from django_filters.rest_framework import filters as drf_filters
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.serializers import IntegerField, URLField, ValidationError

from pulpcore.plugin.actions import ModifyRepositoryActionMixin
from pulpcore.plugin.models import RepositoryVersion
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

from pulp_maven.app.catalog import (
    apply_package_prefix_filters,
    assemble_package_index,
    base_version_annotation,
    collapse_maven_builds,
    distinct_ga_qs,
    maven_packages_in_version,
    repository_metrics,
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
    MavenRepositoryMetricsSerializer,
    MavenRepositoryPackageSerializer,
    MavenRepositorySerializer,
    RepositoryAddCachedContentSerializer,
)
from pulp_maven.app.tasks import add_cached_content_to_repository, repair_metadata
from pulp_maven.app.versions import strip_build_suffix


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
    def upload(self, request, **kwargs):
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
    def upload(self, request, **kwargs):
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

    collapse_builds = drf_filters.BooleanFilter(
        method="filter_collapse_builds",
        help_text=(
            "When true, collapse rebuilds of the same logical version: strip a trailing "
            r"suffix matching \.[a-zA-Z]+-\d+$ from version, then keep one MavenPackage "
            "per (group_id, artifact_id, base_version) with the latest pulp_created. "
            "Default false."
        ),
    )
    base_version = CharFilter(
        method="filter_base_version",
        help_text=(
            "Match units whose version strips to this logical version "
            r"(same suffix as collapse_builds: \.[a-zA-Z]+-\d+$). "
            "5.3.18 matches 5.3.18 and 5.3.18.rhlw-00003, but not 5.3.180."
        ),
    )

    def filter_collapse_builds(self, qs, name, value):
        """Documented on the FilterSet; applied in the viewset after ordering.

        DISTINCT ON requires ORDER BY to start with the distinct columns. The
        viewset applies collapse after other filter backends so that ordering
        cannot break it.
        """
        return qs

    def filter_base_version(self, qs, name, value):
        if not value:
            return qs
        value = strip_build_suffix(value)
        return qs.annotate(_filter_base_version=base_version_annotation()).filter(
            _filter_base_version=value
        )

    class Meta:
        model = MavenPackage
        fields = {  # noqa: RUF012
            "group_id": ["exact"],
            "artifact_id": ["exact"],
            "version": ["exact", "startswith"],
            "name": ["exact"],
            "packaging": ["exact"],
        }


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

    def filter_queryset(self, queryset):
        """Apply ``collapse_builds`` after other backends so DISTINCT ON stays valid."""
        queryset = super().filter_queryset(queryset)
        if getattr(self, "action", "") != "list":
            return queryset
        raw = self.request.query_params.get("collapse_builds")
        if raw is None or raw == "":
            return queryset
        if str(raw).lower() in ("true", "t", "yes", "y", "1"):
            return collapse_maven_builds(queryset)
        return queryset

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
                "action": ["retrieve", "packages", "metrics"],
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

    def filter_queryset(self, queryset):
        """Do not apply the repository FilterSet to package-index query params."""
        if getattr(self, "action", None) in ("packages", "metrics"):
            return queryset
        return super().filter_queryset(queryset)

    def _requested_repository_version(self, repository):
        """Resolve optional ``repository_version`` href/PRN, else latest complete version."""
        href = self.request.query_params.get("repository_version")
        if not href:
            return repository.latest_version()
        repo_version = self.get_resource(href, RepositoryVersion)
        if repo_version.repository_id != repository.pk:
            raise ValidationError({"repository_version": "Must be a version of this repository."})
        return repo_version

    @extend_schema(
        summary="List packages",
        description=(
            "Return one row per distinct (group_id, artifact_id) in a repository version "
            "(latest complete version if repository_version is omitted). "
            "Pagination count is the number of distinct packages, not GAVs. "
            "Each row includes versions (logical version keys after rebuild-suffix strip) "
            "and latest_releases (newest rebuild per logical version). "
            "set(versions) === set(latest_releases[].version)."
        ),
        parameters=[
            OpenApiParameter(
                name="repository_version",
                type=OpenApiTypes.URI,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "HREF or PRN of a version of this repository. "
                    "Defaults to the latest complete version."
                ),
            ),
            OpenApiParameter(
                name="group_id__istartswith",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Case-insensitive prefix on group_id.",
            ),
            OpenApiParameter(
                name="artifact_id__istartswith",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Case-insensitive prefix on artifact_id.",
            ),
        ],
        responses={
            200: inline_serializer(
                name="PaginatedMavenRepositoryPackageList",
                fields={
                    "count": IntegerField(),
                    "next": URLField(allow_null=True),
                    "previous": URLField(allow_null=True),
                    "results": MavenRepositoryPackageSerializer(many=True),
                },
            )
        },
    )
    @action(
        detail=True,
        methods=["get"],
        serializer_class=MavenRepositoryPackageSerializer,
    )
    def packages(self, request, pk, **kwargs):
        """List distinct packages in a repository version."""
        repository = self.get_object()
        repo_version = self._requested_repository_version(repository)
        content_qs = maven_packages_in_version(repo_version)
        content_qs = apply_package_prefix_filters(
            content_qs,
            group_id_prefix=request.query_params.get("group_id__istartswith"),
            artifact_id_prefix=request.query_params.get("artifact_id__istartswith"),
        )
        names_qs = distinct_ga_qs(content_qs)
        page = self.paginate_queryset(names_qs)
        rows = assemble_package_index(
            content_qs,
            page if page is not None else list(names_qs),
            repository,
            repo_version,
        )
        serializer = self.get_serializer(rows, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @extend_schema(
        summary="Repository metrics",
        description=(
            "Distinct counts for MavenPackage content in a repository version "
            "(latest complete version if repository_version is omitted). "
            "package_count is distinct (group_id, artifact_id). version_count is distinct "
            "(group_id, artifact_id, base_version) after rebuild-suffix strip. "
            "build_count is distinct (group_id, artifact_id, full version). "
            "Counts use MavenPackage (POM-backed GAV), not MavenArtifact files."
        ),
        parameters=[
            OpenApiParameter(
                name="repository_version",
                type=OpenApiTypes.URI,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "HREF or PRN of a version of this repository. "
                    "Defaults to the latest complete version."
                ),
            ),
        ],
        responses={200: MavenRepositoryMetricsSerializer},
    )
    @action(
        detail=True,
        methods=["get"],
        serializer_class=MavenRepositoryMetricsSerializer,
    )
    def metrics(self, request, pk, **kwargs):
        """Return package / version / build counts for a repository version."""
        repository = self.get_object()
        repo_version = self._requested_repository_version(repository)
        counts = repository_metrics(maven_packages_in_version(repo_version))
        serializer = self.get_serializer(counts)
        return Response(serializer.data)

    @extend_schema(
        description="Trigger an asynchronous task to add cached content to a repository.",
        summary="Add cached content",
        responses={202: AsyncOperationResponseSerializer},
    )
    @action(detail=True, methods=["post"], serializer_class=RepositoryAddCachedContentSerializer)
    def add_cached_content(self, request, pk, **kwargs):
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
    def repair_metadata(self, request, pk, **kwargs):
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
