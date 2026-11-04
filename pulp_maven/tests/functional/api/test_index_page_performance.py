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

    deferred_type_queries = [
        q
        for q in captured
        if '"core_content"."pulp_type"' in q["sql"].lower()
        and 'where "maven_mavenindexpage"."content_ptr_id" =' in q["sql"].lower()
    ]
    assert not deferred_type_queries, (
        "_generate_index_pages deferred pulp_type while loading existing index pages, "
        f"causing {len(deferred_type_queries)} extra queries (one per page)."
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


@pytest.mark.parallel
def test_descendant_only_add_regenerates_minimal_index_pages(
    maven_repo_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    django_db_blocker,
):
    """Adding content below an already-listed child must not re-render ancestor pages.

    Reproduction for pulp_maven#575. ``_generate_index_pages`` marks every ancestor
    directory of a changed path as affected and re-parses + re-renders each one. For a
    directory with large fan-out (e.g. a popular group/artifact dir listing thousands of
    children), re-rendering it on every add that merely touches a descendant is
    O(fan-out) wasted work and makes ``finalize_new_version`` take minutes on large
    repositories.

    A directory is first populated with several version subdirectories, then a new *file*
    is added under one *existing* version subdirectory. That add does not change the set of
    immediate children of any ancestor directory — only the leaf ``1.0/`` directory gains
    an entry — so only that leaf page needs regenerating. Buggy code regenerates every
    ancestor page (root, ``com/``, ``com/<uid>/`` and the artifact directory) as well.
    """
    from pulp_maven.app.models import MavenArtifact, MavenRepository

    repo = maven_repo_factory()
    uid = _uid()
    artifact_dir = f"com/{uid}/biglib"

    # Populate the artifact directory with several version subdirectories through the API
    # so the real finalize builds index pages for every ancestor directory.
    first_batch = []
    for minor in range(10):
        version = f"1.{minor}"
        artifact = random_artifact_factory(size=32)
        content = maven_artifact_api_client.upload(
            artifact=artifact.pulp_href,
            relative_path=f"{artifact_dir}/{version}/biglib-{version}.jar",
        )
        first_batch.append(content.pulp_href)
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": first_batch}).task
    )

    # A new file under an EXISTING version subdirectory: a descendant-only change.
    artifact = random_artifact_factory(size=32)
    deep = maven_artifact_api_client.upload(
        artifact=artifact.pulp_href,
        relative_path=f"{artifact_dir}/1.0/biglib-1.0-sources.jar",
    )

    # Exercise the incremental finalize path in-process against the live database and
    # record which directory index pages it actually renders. Re-rendering an ancestor page
    # is the O(fan-out) work we must avoid; it is measured by wrapping the real
    # ``Handler.render_html`` with a pass-through counter (the real renderer still runs).
    # A throwaway version is used purely to measure, then discarded.
    from pulpcore.plugin.content import Handler

    rendered_paths = []
    original_render_html = Handler.render_html

    def _recording_render_html(directory_list, path="", dates=None, sizes=None):
        rendered_paths.append(path)
        return original_render_html(directory_list, path=path, dates=dates, sizes=sizes)

    with django_db_blocker.unblock():
        repository = MavenRepository.objects.get(pk=_pk(repo.pulp_href))
        new_version = repository.new_version()
        Handler.render_html = staticmethod(_recording_render_html)
        try:
            new_version.add_content(MavenArtifact.objects.filter(pk__in=[_pk(deep.pulp_href)]))
            repository._generate_index_pages(new_version)
        finally:
            Handler.render_html = staticmethod(original_render_html)
            new_version.delete()

    assert set(rendered_paths) == {f"{artifact_dir}/1.0/"}, (
        f"descendant-only add rendered index pages for {sorted(rendered_paths)}; only "
        f"'{artifact_dir}/1.0/' gained an immediate child. Ancestor directories still list "
        f"the same children, so re-parsing and re-rendering their index pages — whose cost "
        f"scales with their fan-out — is wasted work that dominates finalize on large repos."
    )


@pytest.mark.parallel
def test_new_child_in_existing_directory_updates_parent_listing(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """A new immediate child added to a pre-existing directory must appear in its listing.

    Completeness guard for the #575 optimization (the mirror of
    ``test_descendant_only_add_regenerates_minimal_index_pages``): skipping ancestor pages on
    descendant-only changes must NOT skip a pre-existing directory that gains a brand-new
    immediate child directory. If pruning were too aggressive, the new child would be missing
    from the parent listing.
    """
    from urllib.parse import urljoin

    from pulp_maven.tests.functional.utils import download_file

    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()
    artifact_dir = f"com/{uid}/mylib"

    # Version 1: populate the directory so it (and all its ancestors) already exist.
    first = maven_artifact_api_client.upload(
        artifact=random_artifact_factory(size=32).pulp_href,
        relative_path=f"{artifact_dir}/1.0/mylib-1.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [first.pulp_href]}).task
    )

    listing = download_file(urljoin(base_url, f"{artifact_dir}/"))
    assert listing.response_obj.status == 200
    assert b"1.0/" in listing.body
    assert b"2.0/" not in listing.body

    # Version 2: add a brand-new immediate child directory (2.0/) to the pre-existing parent.
    second = maven_artifact_api_client.upload(
        artifact=random_artifact_factory(size=32).pulp_href,
        relative_path=f"{artifact_dir}/2.0/mylib-2.0.jar",
    )
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": [second.pulp_href]}).task
    )

    updated = download_file(urljoin(base_url, f"{artifact_dir}/"))
    assert updated.response_obj.status == 200
    assert b"2.0/" in updated.body, (
        f"'{artifact_dir}/' did not list its new child '2.0/' after it was added. The "
        f"ancestor-skip optimization must still regenerate a pre-existing directory that "
        f"gains a brand-new immediate child."
    )
    assert b"1.0/" in updated.body  # the pre-existing child must be preserved


@pytest.mark.parallel
def test_removing_last_child_content_updates_parent_listing(
    maven_repo_factory,
    maven_distribution_factory,
    maven_artifact_api_client,
    maven_repo_api_client,
    random_artifact_factory,
    monitor_task,
    distribution_base_url,
):
    """Removing the last content under a child directory must drop it from the parent listing.

    Completeness guard for the #575 optimization: a removal must regenerate the affected
    ancestor pages so an emptied child directory disappears. The pruning only skips
    directories whose changes are additions of already-listed children; any removal forces
    regeneration.
    """
    from urllib.parse import urljoin

    from pulp_maven.tests.functional.utils import download_file

    repo = maven_repo_factory()
    distro = maven_distribution_factory(repository=repo.pulp_href)
    base_url = distribution_base_url(distro.base_url)
    uid = _uid()
    artifact_dir = f"com/{uid}/lib"

    hrefs = {}
    for version in ("1.0", "2.0"):
        content = maven_artifact_api_client.upload(
            artifact=random_artifact_factory(size=32).pulp_href,
            relative_path=f"{artifact_dir}/{version}/lib-{version}.jar",
        )
        hrefs[version] = content.pulp_href
    monitor_task(
        maven_repo_api_client.modify(
            repo.pulp_href, {"add_content_units": list(hrefs.values())}
        ).task
    )

    listing = download_file(urljoin(base_url, f"{artifact_dir}/"))
    assert b"1.0/" in listing.body
    assert b"2.0/" in listing.body

    # Remove the only content under 2.0/, emptying that child directory.
    monitor_task(
        maven_repo_api_client.modify(repo.pulp_href, {"remove_content_units": [hrefs["2.0"]]}).task
    )

    updated = download_file(urljoin(base_url, f"{artifact_dir}/"))
    assert b"2.0/" not in updated.body, (
        f"'{artifact_dir}/' still lists '2.0/' after its only content was removed. A removal "
        f"must regenerate affected ancestor pages so emptied child directories disappear."
    )
    assert b"1.0/" in updated.body  # the remaining child must be preserved
