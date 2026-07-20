from pathlib import PurePath
from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http.response import (
    Http404,
    StreamingHttpResponse,
)
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotAcceptable
from rest_framework.renderers import JSONRenderer, TemplateHTMLRenderer
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from pulpcore.plugin.util import get_domain

from pulp_maven.app.models import MavenArtifact, MavenDistribution
from pulp_maven.app.simple.serializers import MavenPackageMetadataSerializer
from pulp_maven.app.simple.utils import (
    MAVEN_LAST_SERIAL,
    MAVEN_SERIAL_CONSTANT,
    maven_content_to_json,
    write_simple_index,
    write_simple_index_json,
)

MAVEN_SIMPLE_V1_HTML = "application/vnd.maven.simple.v1+html"
MAVEN_SIMPLE_V1_JSON = "application/vnd.maven.simple.v1+json"

ORIGIN_HOST = settings.CONTENT_ORIGIN if settings.CONTENT_ORIGIN else settings.MAVEN_API_HOSTNAME
BASE_CONTENT_URL = urljoin(ORIGIN_HOST, settings.CONTENT_PATH_PREFIX)
BASE_API_URL = urljoin(settings.MAVEN_API_HOSTNAME, settings.MAVEN_PATH_PREFIX)


class MavenSimpleHTMLRenderer(TemplateHTMLRenderer):
    media_type = MAVEN_SIMPLE_V1_HTML


class MavenSimpleJSONRenderer(JSONRenderer):
    media_type = MAVEN_SIMPLE_V1_JSON


class MavenMixin:
    """Mixin to get index specific info."""

    _distro = None

    @property
    def distribution(self):
        if self._distro:
            return self._distro

        path = self.kwargs["path"]
        distro = self.get_distribution(path)
        self._distro = distro
        return distro

    @staticmethod
    def get_distribution(path):
        """Finds the distribution associated with this base_path."""
        try:
            return MavenDistribution.objects.select_related(
                "repository",
            ).get(base_path=path, pulp_domain=get_domain())
        except ObjectDoesNotExist:
            raise Http404(f"No MavenDistribution found for base_path {path}")

    @staticmethod
    def get_repository_version(distribution):
        """Finds the repository version this distribution is serving."""
        rep = distribution.repository
        if rep:
            return rep.latest_version()
        raise Http404("No repository associated with this distribution")

    @staticmethod
    def get_content(repository_version):
        """Returns queryset of the content in this repository version."""
        return MavenArtifact.objects.filter(pk__in=repository_version.content)

    def initial(self, request, *args, **kwargs):
        """Perform common initialization tasks for Maven endpoints."""
        super().initial(request, *args, **kwargs)
        domain_name = get_domain().name
        if settings.DOMAIN_ENABLED:
            self.base_content_url = urljoin(BASE_CONTENT_URL, f"{domain_name}/")
            self.base_api_url = urljoin(BASE_API_URL, f"{domain_name}/")
        else:
            self.base_content_url = BASE_CONTENT_URL
            self.base_api_url = BASE_API_URL

    @classmethod
    def urlpattern(cls):
        """Mocking NamedModelViewSet behavior to get APIs to support RBAC access polices."""
        return f"maven/{cls.endpoint_name}"


class SimpleView(MavenMixin, ViewSet):
    """View for the Maven simple API."""

    endpoint_name = "simple"
    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["list"],
                "principal": "*",
                "effect": "allow",
            },
        ],
    }

    def perform_content_negotiation(self, request, force=False):
        """
        Uses standard content negotiation, defaulting to HTML if no acceptable renderer is found.
        """
        try:
            return super().perform_content_negotiation(request, force)
        except NotAcceptable:
            return TemplateHTMLRenderer(), TemplateHTMLRenderer.media_type

    def get_renderers(self):
        """
        Uses custom renderers for Maven Simple API endpoints.
        """
        return [TemplateHTMLRenderer(), MavenSimpleHTMLRenderer(), MavenSimpleJSONRenderer()]

    @extend_schema(summary="Get index simple page")
    def list(self, request, path):
        """Gets the simple api page listing all group_id:artifact_id projects."""
        repo_version = self.get_repository_version(self.distribution)
        content = self.get_content(repo_version)

        projects = (
            content.order_by("group_id", "artifact_id")
            .values_list("group_id", "artifact_id")
            .distinct()
        )
        project_names = [f"{g}:{a}" for g, a in projects.iterator()]

        media_type = request.accepted_renderer.media_type
        headers = {MAVEN_LAST_SERIAL: str(MAVEN_SERIAL_CONSTANT)}

        if media_type == MAVEN_SIMPLE_V1_JSON:
            return Response(write_simple_index_json(project_names), headers=headers)
        else:
            index_data = write_simple_index(project_names, streamed=True)
            kwargs = {"content_type": media_type, "headers": headers}
            return StreamingHttpResponse(index_data, **kwargs)


class MetadataView(MavenMixin, ViewSet):
    """View for the Maven JSON metadata endpoint."""

    endpoint_name = "maven"
    DEFAULT_ACCESS_POLICY = {
        "statements": [
            {
                "action": ["retrieve"],
                "principal": "*",
                "effect": "allow",
            },
        ],
    }

    @extend_schema(
        tags=["Maven: Metadata"],
        responses={200: MavenPackageMetadataSerializer},
        summary="Get artifact metadata",
    )
    def retrieve(self, request, path, meta):
        """
        Retrieves artifact metadata as JSON.
        `meta` must be a path in form of `{group_id}:{artifact_id}/json/`
        or `{group_id}:{artifact_id}/{version}/json/`
        """
        repo_version = self.get_repository_version(self.distribution)
        content = self.get_content(repo_version)

        meta_path = PurePath(meta)
        package = None
        version = None
        if meta_path.match("*/*/json"):
            package = meta_path.parts[0]
            version = meta_path.parts[1]
        elif meta_path.match("*/json"):
            package = meta_path.parts[0]

        if not package or ":" not in package:
            return Response(status=404)

        group_id, artifact_id = package.split(":", 1)
        filtered = content.filter(group_id=group_id, artifact_id=artifact_id)

        domain = get_domain() if settings.DOMAIN_ENABLED else None
        headers = {MAVEN_LAST_SERIAL: str(MAVEN_SERIAL_CONSTANT)}
        json_body = maven_content_to_json(
            self.distribution.base_path, filtered, version=version, domain=domain
        )
        if json_body:
            return Response(data=json_body, headers=headers)
        return Response(status=404)
