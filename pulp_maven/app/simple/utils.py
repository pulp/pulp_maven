from collections import defaultdict

from django.conf import settings
from jinja2 import Environment

SIMPLE_API_VERSION = "1.1"
MAVEN_SERIAL_CONSTANT = 1000000000
MAVEN_LAST_SERIAL = "X-Maven-Last-Serial"

simple_index_template = """<!DOCTYPE html>
<html>
  <head>
    <title>Simple Index</title>
    <meta name="maven:repository-version" content="{{ SIMPLE_API_VERSION }}">
  </head>
  <body>
    {% for name in projects %}
      <a href="{{ name }}/">{{ name }}</a><br/>
    {% endfor %}
  </body>
</html>
"""


_jinja_env = Environment(autoescape=True)
_simple_template = _jinja_env.from_string(simple_index_template)


def write_simple_index(project_names, streamed=False):
    context = {
        "SIMPLE_API_VERSION": SIMPLE_API_VERSION,
        "projects": project_names,
    }
    return _simple_template.stream(**context) if streamed else _simple_template.render(**context)


def write_simple_index_json(project_names):
    return {
        "meta": {"api-version": SIMPLE_API_VERSION, "_last-serial": MAVEN_SERIAL_CONSTANT},
        "projects": [
            {"name": name, "_last-serial": MAVEN_SERIAL_CONSTANT} for name in project_names
        ],
    }


def maven_content_to_json(base_path, content_query, version=None, domain=None):
    """Converts a QuerySet of MavenArtifact into a JSON metadata response."""
    prefetched = content_query.prefetch_related("contentartifact_set__artifact")

    if version:
        versioned = prefetched.filter(version=version)
        if not versioned.exists():
            return None
        latest_version = version
        latest_content = versioned
    else:
        latest_version = prefetched.order_by("-version").values_list("version", flat=True).first()
        if not latest_version:
            return None
        latest_content = prefetched.filter(version=latest_version)

    first = prefetched.first()
    return {
        "last_serial": 0,
        "info": maven_content_to_info(first.group_id, first.artifact_id, latest_version),
        "releases": maven_content_to_releases(prefetched, base_path, domain),
        "urls": [maven_content_to_download_info(a, base_path, domain) for a in latest_content],
    }


def maven_content_to_info(group_id, artifact_id, version):
    return {
        "name": f"{group_id}:{artifact_id}",
        "group_id": group_id,
        "artifact_id": artifact_id,
        "version": version,
    }


def maven_content_to_releases(content_query, base_path, domain=None):
    releases = defaultdict(list)
    for artifact in content_query.iterator():
        releases[artifact.version].append(
            maven_content_to_download_info(artifact, base_path, domain)
        )
    return releases


def maven_content_to_download_info(artifact, base_path, domain=None):
    ca = artifact.contentartifact_set.select_related("artifact").first()
    sha256 = ca.artifact.sha256 if ca and ca.artifact else ""
    size = ca.artifact.size if ca and ca.artifact else None

    origin = settings.CONTENT_ORIGIN or settings.MAVEN_API_HOSTNAME or ""
    origin = origin.strip("/")
    prefix = settings.CONTENT_PATH_PREFIX.strip("/")
    base_path = base_path.strip("/")
    components = [origin, prefix, base_path, artifact.filename]
    if domain:
        components.insert(2, domain.name)
    url = "/".join(components)

    return {
        "filename": artifact.filename,
        "group_id": artifact.group_id,
        "artifact_id": artifact.artifact_id,
        "version": artifact.version,
        "digests": {"sha256": sha256},
        "size": size,
        "upload_time": str(artifact.pulp_created),
        "upload_time_iso_8601": str(artifact.pulp_created.isoformat()),
        "url": url,
    }
