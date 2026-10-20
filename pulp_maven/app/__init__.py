from gettext import gettext as _

from django.db.models.signals import post_migrate

from pulpcore.plugin import PulpPluginAppConfig


class PulpMavenPluginAppConfig(PulpPluginAppConfig):
    """Entry point for the maven plugin."""

    name = "pulp_maven.app"
    label = "maven"
    version = "0.36.0.dev"
    python_package_name = "pulp-maven"
    domain_compatible = True

    def ready(self):
        """Register the deploy API access policy hook."""
        super().ready()
        post_migrate.connect(
            _populate_maven_access_policies,
            sender=self,
            dispatch_uid="populate_maven_deploy_access_policies_identifier",
        )


def _populate_maven_access_policies(sender, apps, verbosity, **kwargs):
    # The Maven deploy endpoint is a non-model ViewSet routed directly in urls.py, so it is not
    # covered by pulpcore's router-based access-policy registration. Seed it here, keyed by its
    # urlpattern(), leaving any operator customization intact.
    from pulp_maven.app.maven_deploy_api import MavenApiViewSet

    try:
        AccessPolicy = apps.get_model("core", "AccessPolicy")
    except LookupError:
        if verbosity >= 1:
            print(_("AccessPolicy model does not exist. Skipping initialization."))
        return

    viewset_name = MavenApiViewSet.urlpattern()
    access_policy = MavenApiViewSet.DEFAULT_ACCESS_POLICY
    db_access_policy, created = AccessPolicy.objects.get_or_create(
        viewset_name=viewset_name, defaults=access_policy
    )
    if not created and not db_access_policy.customized:
        dirty = False
        for key in ["statements", "creation_hooks", "queryset_scoping"]:
            value = access_policy.get(key)
            if getattr(db_access_policy, key, None) != value:
                setattr(db_access_policy, key, value)
                dirty = True
        if dirty:
            db_access_policy.save()
