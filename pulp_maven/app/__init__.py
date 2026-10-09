from gettext import gettext as _

from django.db.models.signals import post_migrate

from pulpcore.plugin import PulpPluginAppConfig


class PulpMavenPluginAppConfig(PulpPluginAppConfig):
    """Entry point for the maven plugin."""

    name = "pulp_maven.app"
    label = "maven"
    version = "0.35.0.dev"
    python_package_name = "pulp-maven"
    domain_compatible = True

    def ready(self):
        super().ready()
        post_migrate.connect(
            _populate_maven_deploy_access_policies,
            sender=self,
            dispatch_uid="populate_maven_deploy_access_policies",
        )


def _populate_maven_deploy_access_policies(sender, apps, verbosity, **kwargs):
    """
    Create or update the AccessPolicy record for the deploy API.

    pulpcore only auto-discovers viewsets bound to a model, so a plain APIView
    needs its policy registered here or it would have no policy to enforce at
    all. Creates the record from the view's ``DEFAULT_ACCESS_POLICY`` on first
    run and keeps it in step with the code afterwards, unless an administrator
    has customized it.
    """
    from pulp_maven.app.maven_deploy_api import MavenApiViewSet

    try:
        AccessPolicy = apps.get_model("core", "AccessPolicy")
    except LookupError:
        if verbosity >= 1:
            print(_("AccessPolicy model does not exist. Skipping initialization."))
        return

    access_policy = getattr(MavenApiViewSet, "DEFAULT_ACCESS_POLICY", None)
    if access_policy is None:
        return

    viewset_name = MavenApiViewSet.urlpattern()
    db_access_policy, created = AccessPolicy.objects.get_or_create(
        viewset_name=viewset_name, defaults=access_policy
    )
    if created:
        if verbosity >= 1:
            print(f"Access policy for {viewset_name} created.")
    elif not db_access_policy.customized:
        dirty = False
        for key in ["statements", "creation_hooks", "queryset_scoping"]:
            value = access_policy.get(key)
            if getattr(db_access_policy, key, None) != value:
                setattr(db_access_policy, key, value)
                dirty = True
        if dirty:
            db_access_policy.save()
            if verbosity >= 1:
                print(f"Access policy for {viewset_name} updated.")
