import socket

MAVEN_API_HOSTNAME = "https://" + socket.getfqdn()
MAVEN_PATH_PREFIX = "/api/maven/"

DRF_ACCESS_POLICY = {
    "dynaconf_merge_unique": True,
    "reusable_conditions": ["pulp_maven.app.global_access_conditions"],
}
