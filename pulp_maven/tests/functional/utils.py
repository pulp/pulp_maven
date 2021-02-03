"""Utilities for tests for the maven plugin."""
from functools import partial
from unittest import SkipTest

from pulp_smash import api, selectors
from pulp_smash.pulp3.utils import (
    gen_remote,
    gen_repo,
    get_content,
    require_pulp_3,
    require_pulp_plugins,
    sync,
)

from pulp_maven.tests.functional.constants import (
    MAVEN_CONTENT_NAME,
    MAVEN_CONTENT_PATH,
    MAVEN_FIXTURE_URL,
    MAVEN_REMOTE_PATH,
    MAVEN_REPO_PATH,
)


def set_up_module():
    """Skip tests Pulp 3 isn't under test or if pulp_maven isn't installed."""
    require_pulp_3(SkipTest)
    require_pulp_plugins({"pulp_maven"}, SkipTest)


def gen_maven_remote(**kwargs):
    """Return a semi-random dict for use in creating a maven Remote.

    :param url: The URL of an external content source.
    """
    remote = gen_remote(MAVEN_FIXTURE_URL)
    maven_extra_fields = {**kwargs}
    remote.update(**maven_extra_fields)
    return remote


def get_maven_content_paths(repo):
    """
    Return a list of content in the fixture repo.
    """
    return ["academy/alex/custommatcher/1.0/custommatcher-1.0-javadoc.jar.sha1"]


def get_maven_content_unit_paths(repo):
    """Return the relative path of content units present in a maven repository.

    :param repo: A dict of information about the repository.
    :returns: A list with the paths of units present in a given repository.
    """
    # FIXME: The "relative_path" is actually a file path and name
    # It's just an example -- this needs to be replaced with an implementation that works
    # for repositories of this content type.
    return [content_unit["relative_path"] for content_unit in get_content(repo)[MAVEN_CONTENT_NAME]]


def gen_maven_content_attrs(artifact):
    """Generate a dict with content unit attributes.

    :param: artifact: A dict of info about the artifact.
    :returns: A semi-random dict for use in creating a content unit.
    """
    # FIXME: Add content specific metadata here.
    return {"_artifact": artifact["pulp_href"]}


def populate_pulp(cfg, url=MAVEN_FIXTURE_URL):
    """Add maven contents to Pulp.

    :param pulp_smash.config.PulpSmashConfig: Information about a Pulp application.
    :param url: The maven repository URL. Defaults to
        :data:`pulp_smash.constants.MAVEN_FIXTURE_URL`
    :returns: A list of dicts, where each dict describes one file content in Pulp.
    """
    client = api.Client(cfg, api.json_handler)
    remote = {}
    repo = {}
    try:
        remote.update(client.post(MAVEN_REMOTE_PATH, gen_maven_remote(url)))
        repo.update(client.post(MAVEN_REPO_PATH, gen_repo()))
        sync(cfg, remote, repo)
    finally:
        if remote:
            client.delete(remote["pulp_href"])
        if repo:
            client.delete(repo["pulp_href"])
    return client.get(MAVEN_CONTENT_PATH)["results"]


skip_if = partial(selectors.skip_if, exc=SkipTest)
"""The ``@skip_if`` decorator, customized for unittest.

:func:`pulp_smash.selectors.skip_if` is test runner agnostic. This function is
identical, except that ``exc`` has been set to ``unittest.SkipTest``.
"""
