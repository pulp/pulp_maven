from django.conf import settings


def index_has_perm(request, view, action, perm="maven.view_mavendistribution"):
    """Access Policy condition that checks if the user has the perm on the distribution."""
    if request.user.has_perm(perm):
        return True
    if settings.DOMAIN_ENABLED:
        if request.user.has_perm(perm, obj=request.pulp_domain):
            return True
    return request.user.has_perm(perm, obj=view.distribution)
