from pulpcore.plugin import PulpPluginAppConfig


class PulpMavenPluginAppConfig(PulpPluginAppConfig):
    """Entry point for the maven plugin."""

    name = "pulp_maven.app"
    label = "maven"
    version = "0.10.1"
    python_package_name = "pulp-maven"
    domain_compatible = True
