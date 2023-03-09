from django.urls import re_path
from pulp_maven.app.maven_deploy_api import MavenApiViewSet

urlpatterns = [
    re_path(r"^pulp/maven/(?P<name>[\w-]+)/(?P<path>.*)$", MavenApiViewSet.as_view()),
]
