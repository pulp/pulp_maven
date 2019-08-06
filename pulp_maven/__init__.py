import pkg_resources

__version__ = pkg_resources.get_distribution("pulp_maven").version

default_app_config = "pulp_maven.app.PulpMavenPluginAppConfig"
