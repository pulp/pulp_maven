import types
import uuid

import pytest


def _uid():
    return uuid.uuid4().hex[:8]


@pytest.fixture
def ops(
    maven_repo_api_client,
    maven_package_api_client,
    maven_artifact_api_client,
    pom_file_factory,
    monitor_task,
    tmp_path,
):
    """Small bound helpers for the repeated upload / modify / repair / list boilerplate."""

    def add(repo, hrefs):
        monitor_task(
            maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": hrefs}).task
        )
        return maven_repo_api_client.read(repo.pulp_href)

    def remove(repo, hrefs):
        monitor_task(
            maven_repo_api_client.modify(repo.pulp_href, {"remove_content_units": hrefs}).task
        )
        return maven_repo_api_client.read(repo.pulp_href)

    def repair(repo):
        monitor_task(maven_repo_api_client.repair_packages(repo.pulp_href).task)
        return maven_repo_api_client.read(repo.pulp_href)

    def packages(repo):
        return maven_package_api_client.list(repository_version=repo.latest_version_href)

    def package_count(repo):
        return packages(repo).count

    def package_hrefs(repo):
        return [p.pulp_href for p in packages(repo).results]

    def upload_pom(group_id, artifact_id, version):
        path = pom_file_factory(
            group_id=group_id,
            artifact_id=artifact_id,
            version=version,
            name=f"{artifact_id} {version}",
            packaging="jar",
        )
        group_path = group_id.replace(".", "/")
        rel = f"{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.pom"
        return maven_artifact_api_client.upload(file=str(path), relative_path=rel).pulp_href

    def upload_jar(group_id, artifact_id, version):
        path = tmp_path / f"{artifact_id}-{version}.jar"
        path.write_bytes(b"dummy jar bytes, not a real archive")
        group_path = group_id.replace(".", "/")
        rel = f"{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.jar"
        return maven_artifact_api_client.upload(file=str(path), relative_path=rel).pulp_href

    return types.SimpleNamespace(
        add=add,
        remove=remove,
        repair=repair,
        packages=packages,
        package_count=package_count,
        package_hrefs=package_hrefs,
        upload_pom=upload_pom,
        upload_jar=upload_jar,
    )


@pytest.fixture
def stranded_repo(maven_repo_factory, ops):
    """Return a repo whose latest version has POMs but no associated MavenPackages.

    Takes a list of (group_id, artifact_id, version) tuples.
    """

    def _factory(gavs):
        repo = maven_repo_factory()
        uid = _uid()  # unique group per run for @pytest.mark.parallel safety
        hrefs = [
            ops.upload_pom(f"{group}.{uid}", artifact, version) for group, artifact, version in gavs
        ]
        repo = ops.add(repo, hrefs)

        # Packages were auto-associated; strand them by removing the MavenPackage units.
        pkg_hrefs = ops.package_hrefs(repo)
        assert len(pkg_hrefs) == len(gavs)
        repo = ops.remove(repo, pkg_hrefs)
        assert ops.package_count(repo) == 0, "repo should be stranded before repair"
        return repo

    return _factory


@pytest.mark.parallel
def test_repair_packages_empty_repo_noop(maven_repo_factory, ops):
    """repair_packages on an empty repository succeeds and changes nothing."""
    repo = maven_repo_factory()
    version_before = repo.latest_version_href

    repo = ops.repair(repo)

    assert repo.latest_version_href == version_before, "empty repo should get no new version"
    assert ops.package_count(repo) == 0


@pytest.mark.parallel
def test_repair_packages_associates_missing_packages(stranded_repo, ops):
    """repair_packages associates the MavenPackages for stranded POMs in a new version."""
    repo = stranded_repo([("com.example", "alpha", "1.0.0"), ("com.example", "beta", "2.0.0")])
    version_before = repo.latest_version_href

    repo = ops.repair(repo)

    assert repo.latest_version_href != version_before, "repair should create a new version"
    assert ops.package_count(repo) == 2, "both stranded packages should now be associated"


@pytest.mark.parallel
def test_repair_packages_handles_many_packages(stranded_repo, ops):
    """repair_packages associates many stranded packages in one run (exercises the full scan)."""
    repo = stranded_repo([("com.example", f"lib{i:02d}", "1.0.0") for i in range(12)])

    repo = ops.repair(repo)

    assert ops.package_count(repo) == 12


@pytest.mark.parallel
def test_repair_packages_removes_dead_packages(maven_repo_factory, ops):
    """repair_packages removes a version MavenPackage whose GAV no longer has a POM."""
    repo = maven_repo_factory()
    group = f"com.example.{_uid()}"

    pom = ops.upload_pom(group, "dead", "1.0.0")
    repo = ops.add(repo, [pom])
    pkg_hrefs = ops.package_hrefs(repo)
    assert len(pkg_hrefs) == 1

    # Remove the POM artifact (incremental finalize drops the package too) ...
    repo = ops.remove(repo, [pom])
    assert ops.package_count(repo) == 0
    # ... then re-add only the package, leaving a dead membership (package present, no POM).
    repo = ops.add(repo, pkg_hrefs)
    assert ops.package_count(repo) == 1

    repo = ops.repair(repo)
    assert ops.package_count(repo) == 0, "dead package (GAV with no POM) should be removed"


@pytest.mark.parallel
def test_repair_packages_ignores_pomless_gav(maven_repo_factory, ops):
    """repair_packages only backfills POM-backed GAVs; a .jar-only GAV gets no package."""
    repo = maven_repo_factory()
    group = f"com.example.{_uid()}"

    pom = ops.upload_pom(group, "has-pom", "1.0.0")
    jar = ops.upload_jar(group, "no-pom", "1.0.0")
    repo = ops.add(repo, [pom, jar])

    # Strand the single auto-created package (has-pom); no-pom never had one.
    pkg_hrefs = ops.package_hrefs(repo)
    assert len(pkg_hrefs) == 1
    repo = ops.remove(repo, pkg_hrefs)
    assert ops.package_count(repo) == 0

    repo = ops.repair(repo)
    packages = ops.packages(repo)
    assert packages.count == 1, "only the POM-backed GAV should get a package"
    assert packages.results[0].artifact_id == "has-pom", "the .jar-only GAV must not get a package"


@pytest.mark.parallel
def test_repair_packages_is_idempotent(stranded_repo, ops):
    """A second repair_packages run finds nothing stranded and creates no new version."""
    repo = stranded_repo([("com.example", "gamma", "1.0.0")])

    repo = ops.repair(repo)
    version_after_repair = repo.latest_version_href
    assert ops.package_count(repo) == 1

    repo = ops.repair(repo)
    assert repo.latest_version_href == version_after_repair, "second run must not create a version"
    assert ops.package_count(repo) == 1
