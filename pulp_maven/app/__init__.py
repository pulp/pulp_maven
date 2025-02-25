from pulpcore.plugin import PulpPluginAppConfig


class PulpMavenPluginAppConfig(PulpPluginAppConfig):
    """Entry point for the maven plugin."""

    name = "pulp_maven.app"
    label = "maven"
    version = "0.8.4.dev"
    python_package_name = "pulp-maven"
