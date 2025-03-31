from django.conf import settings
from django.urls import re_path
from pulp_maven.app.maven_deploy_api import MavenApiViewSet

if settings.DOMAIN_ENABLED:
    path_re = r"(?P<pulp_domain>[-a-zA-Z0-9_]+)/(?P<name>[\w-]+)/(?P<path>.*)"
else:
    path_re = r"(?P<name>[\w-]+)/(?P<path>.*)"

urlpatterns = [
    re_path(rf"^pulp/maven/{path_re}$", MavenApiViewSet.as_view()),
]
