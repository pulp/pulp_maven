from django.conf import settings

from pulpcore.plugin.util import get_domain

from pulp_maven.app.models import MavenRepository


def maven_has_repository_perm(request, view, action, perm):
    """
    Check ``perm`` against the repository named in the request path.

    The Maven deploy API resolves its repository from the URL rather than from a
    viewset queryset, so the usual object-level checks do not apply and this
    resolves it the same way the handler will.

    Denies when no repository matches the path. Allowing it would turn a
    mistyped or probed repository name into a way past the permission check,
    which matters here because the handler creates content.
    """
    if request.user.has_perm(perm):
        return True
    if settings.DOMAIN_ENABLED and request.user.has_perm(perm, obj=request.pulp_domain):
        return True

    repository = _repository_from_path(request, view)
    if repository is None:
        return False
    return request.user.has_perm(perm, obj=repository)


def _repository_from_path(request, view):
    name = view.kwargs.get("name")
    if not name:
        return None
    return MavenRepository.objects.filter(name=name, pulp_domain=get_domain()).first()
