from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.http.response import (
    Http404,
    StreamingHttpResponse,
)
from rest_framework.exceptions import NotAcceptable
from rest_framework.renderers import JSONRenderer, TemplateHTMLRenderer
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from pulpcore.plugin.util import get_domain

from pulp_maven.app.models import MavenArtifact, MavenDistribution
from pulp_maven.app.simple.utils import (
    SERIAL_CONSTANT,
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
        try:
            return MavenDistribution.objects.select_related(
                "repository",
            ).get(base_path=path, pulp_domain=get_domain())
        except ObjectDoesNotExist:
            raise Http404(f"No MavenDistribution found for base_path {path}")

    @staticmethod
    def get_repository_version(distribution):
        rep = distribution.repository
        if rep:
            return rep.latest_version()
        raise Http404("No repository associated with this distribution")

    @staticmethod
    def get_content(repository_version):
        return MavenArtifact.objects.filter(pk__in=repository_version.content)

    def initial(self, request, *args, **kwargs):
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
        return f"maven/{cls.endpoint_name}"


class SimpleView(MavenMixin, ViewSet):
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
        try:
            return super().perform_content_negotiation(request, force)
        except NotAcceptable:
            return TemplateHTMLRenderer(), TemplateHTMLRenderer.media_type

    def get_renderers(self):
        return [TemplateHTMLRenderer(), MavenSimpleHTMLRenderer(), MavenSimpleJSONRenderer()]

    def list(self, request, path):
        """Gets the simple index page listing all group_id:artifact_id projects."""
        repo_version = self.get_repository_version(self.distribution)
        content = self.get_content(repo_version)

        projects = (
            content.order_by("group_id", "artifact_id")
            .values_list("group_id", "artifact_id")
            .distinct()
        )
        project_names = [f"{g}:{a}" for g, a in projects.iterator()]

        media_type = request.accepted_renderer.media_type
        headers = {"X-PyPI-Last-Serial": str(SERIAL_CONSTANT)}

        if media_type == MAVEN_SIMPLE_V1_JSON:
            return Response(write_simple_index_json(project_names), headers=headers)
        else:
            index_data = write_simple_index(project_names, streamed=True)
            return StreamingHttpResponse(
                index_data, content_type=media_type, headers=headers
            )

