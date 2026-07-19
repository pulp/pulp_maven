from django.conf import settings
from django.urls import path, re_path

from pulp_maven.app.maven_deploy_api import MavenApiViewSet
from pulp_maven.app.simple.views import MetadataView, SimpleView

if settings.DOMAIN_ENABLED:
    path_re = r"(?P<pulp_domain>[-a-zA-Z0-9_]+)/(?P<name>[\w-]+)/(?P<path>.*)"
    MAVEN_API_URL = settings.MAVEN_PATH_PREFIX.strip("/") + "/<slug:pulp_domain>/<path:path>/"
else:
    path_re = r"(?P<name>[\w-]+)/(?P<path>.*)"
    MAVEN_API_URL = settings.MAVEN_PATH_PREFIX.strip("/") + "/<path:path>/"

urlpatterns = [
    # Deploy API (existing)
    re_path(rf"^pulp/maven/{path_re}$", MavenApiViewSet.as_view()),
    # Simple API
    path(
        MAVEN_API_URL + "simple/",
        SimpleView.as_view({"get": "list"}),
        name="maven-simple-detail",
    ),
    # Metadata API
    path(
        MAVEN_API_URL + "maven/<path:meta>/",
        MetadataView.as_view({"get": "retrieve"}),
        name="maven-metadata",
    ),
]
