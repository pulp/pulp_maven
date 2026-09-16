"""Reproduction for pulp_maven#484.

``MavenRepository._generate_index_pages`` regenerates the HTML directory index
pages that were touched by a repository version.  Its incremental path (used by
``finalize_new_version``) issued one ``ContentArtifact`` query *per affected
directory* with ``relative_path__startswith=<dir>``.  The root directory ``""``
is always in the affected set, and ``startswith=""`` matches every
ContentArtifact in the version — so every finalize did at least one full-version
scan, plus one subtree scan per affected directory.  On large repositories this
made ``finalize_new_version`` take tens of minutes.

The fix loads the version's ContentArtifacts in a single query and buckets them
into the affected directories in Python, so no per-directory ``LIKE`` scans are
issued.

The reproduction builds a version whose content spans several nested Maven
directories through the real API, then exercises the real
``_generate_index_pages`` in-process against the live database while capturing
SQL, and asserts that it does not issue one ContentArtifact ``LIKE`` scan per
directory.
"""

import uuid

import pytest
from django.db import connection


def _uid():
    return uuid.uuid4().hex[:8]


def _pk(href):
    """Extract the primary key (UUID) from a pulp resource href."""
    return href.rstrip("/").split("/")[-1]


@pytest.mark.parallel
def test_finalize_does_not_scan_per_directory(
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    django_db_blocker,
):
    """Incremental index-page generation must not scan ContentArtifacts per directory.

    Buggy code issues one ``relative_path LIKE '<dir>%'`` query for every affected
    directory (including the root ``""`` full-version scan).  The fix issues a
    single set-valued ``content_id IN (...)`` scan and zero per-directory ``LIKE``
    scans.
    """
    repo = maven_repo_factory()
    uid = _uid()

    # Content spanning several nested Maven directories, uploaded through the API.
    relative_paths = [
        f"com/{uid}/alpha/1.0/alpha-1.0.jar",
        f"com/{uid}/alpha/1.0/alpha-1.0.pom",
        f"com/{uid}/beta/2.0/beta-2.0.jar",
        f"org/{uid}/gamma/3.1/gamma-3.1.jar",
        f"org/{uid}/delta/4.2/delta-4.2.jar",
    ]
    content_hrefs = []
    for relative_path in relative_paths:
        artifact = random_artifact_factory(size=32)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href, relative_path=relative_path
        )
        content_hrefs.append(content.pulp_href)

    # Establish a baseline version through the API so the version has existing
    # content that the root-directory scan would load.
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs[:-1]}).task
    )

    # Exercise the incremental finalize path in-process against the live database,
    # capturing the SQL it issues.  A throwaway new version is used purely to
    # measure the query pattern; it is discarded afterwards.
    from pulp_maven.app.models import MavenArtifact, MavenRepository

    captured = []
    with django_db_blocker.unblock():
        repository = MavenRepository.objects.get(pk=_pk(repo.pulp_href))
        new_version = repository.new_version()
        try:
            new_version.add_content(
                MavenArtifact.objects.filter(pk__in=[_pk(h) for h in content_hrefs])
            )
            connection.force_debug_cursor = True
            start = len(connection.queries)
            repository._generate_index_pages(new_version)
            captured = connection.queries[start:]
        finally:
            connection.force_debug_cursor = False
            new_version.delete()

    like_scans = [
        q
        for q in captured
        if "core_contentartifact" in q["sql"].lower() and "like" in q["sql"].lower()
    ]
    assert len(like_scans) <= 1, (
        f"_generate_index_pages issued {len(like_scans)} per-directory ContentArtifact "
        f'LIKE scans (one per affected directory, including the root "" full-version '
        f"scan). The incremental path must load ContentArtifacts in a single "
        f"set-valued scan.\n" + "\n".join(q["sql"][:160] for q in like_scans[:8])
    )


@pytest.mark.parallel
def test_index_pages_generated_for_nested_directories(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Adding artifacts still generates correct HTML index pages for each directory.

    Behavioural guard: the single-scan optimisation must produce the same directory
    listings as before — the root lists top-level groups and a leaf directory lists
    its files.
    """
    from urllib.parse import urljoin

    from pulp_maven.tests.functional.utils import download_file

    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()

    content_hrefs = []
    for relative_path in (
        f"com/{uid}/alpha/1.0/alpha-1.0.jar",
        f"com/{uid}/beta/2.0/beta-2.0.jar",
    ):
        artifact = random_artifact_factory(size=32)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href, relative_path=relative_path
        )
        content_hrefs.append(content.pulp_href)

    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": content_hrefs}).task
    )

    # The leaf directory index lists the jar it contains.
    leaf = download_file(urljoin(base_url, f"com/{uid}/alpha/1.0/"))
    assert leaf.response_obj.status == 200
    assert b"alpha-1.0.jar" in leaf.body

    # An intermediate directory index lists its immediate child directory.
    intermediate = download_file(urljoin(base_url, f"com/{uid}/"))
    assert intermediate.response_obj.status == 200
    assert b"alpha/" in intermediate.body
