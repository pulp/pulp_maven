"""Catalog API tests.

Generated client methods are unavailable until `oci-env generate-client` is rerun.
"""

import uuid
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import urljoin

import pytest
import requests


def _uid():
    # Unique groupIds so parallel workers do not upload identical POM bytes
    # (Artifact checksum unique constraint) or collide on MavenArtifact GAV.
    return uuid.uuid4().hex[:8]


def _parse_dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _api_get(bindings_cfg, path, **params):
    url = urljoin(bindings_cfg.host + "/", path.lstrip("/"))
    response = requests.get(url, params=params, auth=(bindings_cfg.username, bindings_cfg.password))
    assert response.status_code == 200, response.text
    return response.json()


def _content_package_path(repo_href):
    marker = "/api/v3/"
    idx = repo_href.find(marker)
    assert idx != -1, repo_href
    return f"{repo_href[: idx + len(marker)]}content/maven/package/"


def _assert_package_row(pkg):
    assert pkg["group_id"]
    assert pkg["artifact_id"]
    assert pkg["last_updated"]
    assert pkg["versions"] == [rel["version"] for rel in pkg["latest_releases"]]
    for rel in pkg["latest_releases"]:
        assert "version" in rel
        assert "release" in rel
        assert rel["created_at"]


def _upload_gavs(
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
    gavs,
):
    repo = maven_repo_factory()
    hrefs = []
    for group_id, artifact_id, version in gavs:
        group_path = group_id.replace(".", "/") + f"/{artifact_id}/{version}"
        pom_path = pom_file_factory(group_id=group_id, artifact_id=artifact_id, version=version)
        content = maven_artifact_api_client.upload(
            file=str(pom_path),
            relative_path=f"{group_path}/{artifact_id}-{version}.pom",
        )
        hrefs.append(content.pulp_href)
    monitor_task(maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": hrefs}).task)
    return maven_repo_api_client.read(repo.pulp_href)


def _add_gavs(
    maven_artifact_api_client,
    maven_repo_api_client,
    pom_file_factory,
    monitor_task,
    repo,
    gavs,
):
    hrefs = []
    for group_id, artifact_id, version in gavs:
        group_path = group_id.replace(".", "/") + f"/{artifact_id}/{version}"
        pom_path = pom_file_factory(group_id=group_id, artifact_id=artifact_id, version=version)
        content = maven_artifact_api_client.upload(
            file=str(pom_path),
            relative_path=f"{group_path}/{artifact_id}-{version}.pom",
        )
        hrefs.append(content.pulp_href)
    monitor_task(maven_repo_api_client.modify(repo.pulp_href, {"add_content_units": hrefs}).task)
    return maven_repo_api_client.read(repo.pulp_href)


@pytest.fixture
def catalog_groups():
    uid = _uid()
    return SimpleNamespace(example=f"com.example.{uid}", other=f"org.other.{uid}")


@pytest.fixture
def catalog_repo(
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
    catalog_groups,
):
    """Three GAs; hello has two versions plus a Lightwell rebuild and a 5.3.180 neighbor."""
    # Upload order sets pulp_created: unsuffixed 5.3.18 first, then the rebuild.
    gavs = [
        (catalog_groups.example, "hello", "5.3.18"),
        (catalog_groups.example, "hello", "5.3.18.rhlw-00003"),
        (catalog_groups.example, "hello", "5.3.180"),
        (catalog_groups.example, "hello", "1.0.0"),
        (catalog_groups.example, "world", "1.0.0"),
        (catalog_groups.other, "widget", "1.0.0"),
    ]
    return _upload_gavs(
        maven_artifact_api_client,
        maven_repo_api_client,
        maven_repo_factory,
        pom_file_factory,
        monitor_task,
        gavs,
    )


@pytest.mark.parallel
def test_package_list_grouping_and_pagination(bindings_cfg, catalog_repo, catalog_groups):
    """Package index is one row per GA, and count is distinct packages not GAVs."""
    data = _api_get(bindings_cfg, f"{catalog_repo.pulp_href}packages/", limit=1)
    assert data["count"] == 3
    assert len(data["results"]) == 1
    _assert_package_row(data["results"][0])

    page2 = _api_get(bindings_cfg, f"{catalog_repo.pulp_href}packages/", limit=1, offset=1)
    assert page2["count"] == 3
    page2_ga = (page2["results"][0]["group_id"], page2["results"][0]["artifact_id"])
    page1_ga = (data["results"][0]["group_id"], data["results"][0]["artifact_id"])
    assert page2_ga != page1_ga

    all_rows = _api_get(bindings_cfg, f"{catalog_repo.pulp_href}packages/", limit=100)["results"]
    gas = {(pkg["group_id"], pkg["artifact_id"]) for pkg in all_rows}
    assert gas == {
        (catalog_groups.example, "hello"),
        (catalog_groups.example, "world"),
        (catalog_groups.other, "widget"),
    }
    hello = next(pkg for pkg in all_rows if pkg["artifact_id"] == "hello")
    _assert_package_row(hello)
    assert hello["versions"] == ["5.3.180", "5.3.18", "1.0.0"]
    assert len(hello["latest_releases"]) == 3
    release_53 = next(rel for rel in hello["latest_releases"] if rel["version"] == "5.3.18")
    assert release_53["release"] == "rhlw-00003"
    for rel in hello["latest_releases"]:
        if rel["version"] != "5.3.18":
            assert rel["release"] == ""


@pytest.mark.parallel
def test_package_list_istartswith(bindings_cfg, catalog_repo):
    """Prefix search is case-insensitive on the package index."""
    data = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", group_id__istartswith="com.example."
    )
    assert data["count"] == 2
    assert {pkg["artifact_id"] for pkg in data["results"]} == {"hello", "world"}

    data = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", group_id__istartswith="COM.EXAMPLE."
    )
    assert data["count"] == 2

    data = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", artifact_id__istartswith="hel"
    )
    assert data["count"] == 1
    assert data["results"][0]["artifact_id"] == "hello"

    data = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", artifact_id__istartswith="HEL"
    )
    assert data["count"] == 1
    assert data["results"][0]["artifact_id"] == "hello"

    data = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", group_id__istartswith="org.missing"
    )
    assert data["count"] == 0


@pytest.fixture
def search_groups():
    uid = _uid()
    return SimpleNamespace(foo=f"foo.bar.{uid}", test=f"org.test.{uid}")


@pytest.fixture
def search_repo(
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
    search_groups,
):
    """Packages for catalog ``search``: foo.bar:bar-junit plus neighbors that must not leak."""
    return _upload_gavs(
        maven_artifact_api_client,
        maven_repo_api_client,
        maven_repo_factory,
        pom_file_factory,
        monitor_task,
        [
            (search_groups.foo, "bar-junit", "1.0.0"),
            (search_groups.foo, "other", "1.0.0"),
            (search_groups.test, "bar-junit", "1.0.0"),
        ],
    )


def _package_gas(data):
    return {(pkg["group_id"], pkg["artifact_id"]) for pkg in data["results"]}


@pytest.mark.parallel
def test_package_list_search(bindings_cfg, search_repo, search_groups):
    """search is contains on group_id/artifact_id; ':' ANDs the two sides."""
    path = f"{search_repo.pulp_href}packages/"
    target = (search_groups.foo, "bar-junit")
    foo_packages = {(search_groups.foo, "bar-junit"), (search_groups.foo, "other")}
    junit_packages = {(search_groups.foo, "bar-junit"), (search_groups.test, "bar-junit")}
    all_packages = foo_packages | {(search_groups.test, "bar-junit")}

    unfiltered = _api_get(bindings_cfg, path)
    assert _package_gas(unfiltered) == all_packages

    # No colon: group contains OR artifact contains (user examples on foo.bar:bar-junit).
    assert _package_gas(_api_get(bindings_cfg, path, search="oo")) == foo_packages
    assert _package_gas(_api_get(bindings_cfg, path, search="ba")) == all_packages
    assert _package_gas(_api_get(bindings_cfg, path, search="junit")) == junit_packages

    # Colon: group contains left AND artifact contains right.
    assert _package_gas(_api_get(bindings_cfg, path, search="foo:junit")) == {target}
    assert _package_gas(_api_get(bindings_cfg, path, search="foo:test")) == set()
    # foo.bar:bar-junit misses (group has no "test"); org.test:bar-junit hits.
    assert _package_gas(_api_get(bindings_cfg, path, search="test:bar")) == {
        (search_groups.test, "bar-junit")
    }

    # Case-insensitive; trim; ignore a trailing version segment.
    assert _package_gas(_api_get(bindings_cfg, path, search="FOO:JUNIT")) == {target}
    assert _package_gas(_api_get(bindings_cfg, path, search=" foo : junit ")) == {target}
    assert _package_gas(_api_get(bindings_cfg, path, search="foo.bar:bar-junit:9.9.9")) == {target}

    # Empty side: group-only or artifact-only; ':' / blank is a no-op.
    assert _package_gas(_api_get(bindings_cfg, path, search="foo:")) == foo_packages
    assert _package_gas(_api_get(bindings_cfg, path, search=":junit")) == junit_packages
    assert _package_gas(_api_get(bindings_cfg, path, search=":")) == all_packages
    assert _package_gas(_api_get(bindings_cfg, path, search="   ")) == all_packages

    # Existing prefix filters still AND with search.
    and_prefix = _api_get(bindings_cfg, path, search="junit", group_id__istartswith="foo")
    assert _package_gas(and_prefix) == {target}


@pytest.mark.parallel
def test_package_list_version_order(
    bindings_cfg,
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
):
    """versions and latest_releases are newest-first by numeric tokens, not lexicographically."""
    # Upload out of order so pulp_created cannot accidentally match the expected sort.
    group_id = f"com.example.{_uid()}"
    repo = _upload_gavs(
        maven_artifact_api_client,
        maven_repo_api_client,
        maven_repo_factory,
        pom_file_factory,
        monitor_task,
        [
            (group_id, "ordered", "1.10"),
            (group_id, "ordered", "1.9"),
            (group_id, "ordered", "1.2"),
        ],
    )
    data = _api_get(bindings_cfg, f"{repo.pulp_href}packages/")
    assert data["count"] == 1
    pkg = data["results"][0]
    _assert_package_row(pkg)
    assert pkg["versions"] == ["1.10", "1.9", "1.2"]
    assert [rel["version"] for rel in pkg["latest_releases"]] == ["1.10", "1.9", "1.2"]


@pytest.mark.parallel
def test_package_list_created_at_is_membership(
    bindings_cfg,
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
):
    """created_at is repository membership time, not the content unit's pulp_created."""
    group_id = f"com.example.{_uid()}"
    repo_a = _upload_gavs(
        maven_artifact_api_client,
        maven_repo_api_client,
        maven_repo_factory,
        pom_file_factory,
        monitor_task,
        [(group_id, "timestamped", "1.0.0")],
    )
    units = _api_get(
        bindings_cfg,
        _content_package_path(repo_a.pulp_href),
        repository_version=repo_a.latest_version_href,
        limit=100,
    )["results"]
    assert len(units) == 1
    unit_created = _parse_dt(units[0]["pulp_created"])

    repo_b = maven_repo_factory()
    monitor_task(
        maven_repo_api_client.modify(
            repo_b.pulp_href, {"add_content_units": [units[0]["pulp_href"]]}
        ).task
    )

    pkgs_a = _api_get(bindings_cfg, f"{repo_a.pulp_href}packages/")["results"]
    pkgs_b = _api_get(bindings_cfg, f"{repo_b.pulp_href}packages/")["results"]
    created_a = _parse_dt(pkgs_a[0]["latest_releases"][0]["created_at"])
    created_b = _parse_dt(pkgs_b[0]["latest_releases"][0]["created_at"])

    assert created_a >= unit_created
    assert created_b > created_a
    assert created_b > unit_created
    assert pkgs_a[0]["last_updated"] == pkgs_a[0]["latest_releases"][0]["created_at"]
    assert pkgs_b[0]["last_updated"] == pkgs_b[0]["latest_releases"][0]["created_at"]
    assert _parse_dt(pkgs_b[0]["last_updated"]) > _parse_dt(pkgs_a[0]["last_updated"])


@pytest.mark.parallel
def test_package_list_empty_repository(bindings_cfg, maven_repo_factory):
    repo = maven_repo_factory()
    data = _api_get(bindings_cfg, f"{repo.pulp_href}packages/")
    assert data["count"] == 0
    assert data["results"] == []


@pytest.mark.parallel
def test_repository_metrics(bindings_cfg, catalog_repo, maven_repo_factory):
    """Metrics count distinct MavenPackage GAs / base versions / full GAVs."""
    data = _api_get(bindings_cfg, f"{catalog_repo.pulp_href}metrics/")
    assert data["package_count"] == 3
    # hello: 1.0.0, 5.3.18, 5.3.180; world 1.0.0; widget 1.0.0
    assert data["version_count"] == 5
    # hello also has 5.3.18.rhlw-00003 as a separate full GAV
    assert data["build_count"] == 6

    empty = _api_get(bindings_cfg, f"{maven_repo_factory().pulp_href}metrics/")
    assert empty == {"package_count": 0, "version_count": 0, "build_count": 0}


@pytest.mark.parallel
def test_packages_and_metrics_repository_version(bindings_cfg, catalog_repo, maven_repo_factory):
    """repository_version selects a snapshot; omitted uses the latest complete version."""
    latest_href = catalog_repo.latest_version_href
    v0_href = f"{catalog_repo.pulp_href}versions/0/"

    default_pkgs = _api_get(bindings_cfg, f"{catalog_repo.pulp_href}packages/")
    explicit_pkgs = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", repository_version=latest_href
    )
    assert default_pkgs["count"] == explicit_pkgs["count"] == 3

    v0_pkgs = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", repository_version=v0_href
    )
    assert v0_pkgs["count"] == 0
    assert v0_pkgs["results"] == []

    default_metrics = _api_get(bindings_cfg, f"{catalog_repo.pulp_href}metrics/")
    explicit_metrics = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}metrics/", repository_version=latest_href
    )
    assert default_metrics == explicit_metrics
    v0_metrics = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}metrics/", repository_version=v0_href
    )
    assert v0_metrics == {"package_count": 0, "version_count": 0, "build_count": 0}

    other = maven_repo_factory()
    url = urljoin(bindings_cfg.host + "/", f"{catalog_repo.pulp_href}packages/".lstrip("/"))
    response = requests.get(
        url,
        params={"repository_version": other.latest_version_href},
        auth=(bindings_cfg.username, bindings_cfg.password),
    )
    assert response.status_code == 400, response.text


@pytest.mark.parallel
def test_collapse_builds_and_base_version(bindings_cfg, catalog_repo, catalog_groups):
    """collapse_builds keeps one unit per logical version; base_version is always present."""
    path = _content_package_path(catalog_repo.pulp_href)
    repo_version = catalog_repo.latest_version_href

    expanded = _api_get(
        bindings_cfg,
        path,
        group_id=catalog_groups.example,
        artifact_id="hello",
        repository_version=repo_version,
        collapse_builds="false",
        limit=100,
    )
    default = _api_get(
        bindings_cfg,
        path,
        group_id=catalog_groups.example,
        artifact_id="hello",
        repository_version=repo_version,
        limit=100,
    )
    collapsed = _api_get(
        bindings_cfg,
        path,
        group_id=catalog_groups.example,
        artifact_id="hello",
        repository_version=repo_version,
        collapse_builds="true",
        limit=100,
    )
    assert expanded["count"] == 4
    assert default["count"] == 4
    assert {item["version"] for item in default["results"]} == {
        "1.0.0",
        "5.3.18",
        "5.3.18.rhlw-00003",
        "5.3.180",
    }
    assert collapsed["count"] == 3
    assert {item["base_version"] for item in collapsed["results"]} == {
        "1.0.0",
        "5.3.18",
        "5.3.180",
    }
    collapsed_rebuild = next(
        item for item in collapsed["results"] if item["base_version"] == "5.3.18"
    )
    assert collapsed_rebuild["version"] == "5.3.18.rhlw-00003"
    for item in expanded["results"]:
        if item["version"] == "5.3.18.rhlw-00003":
            assert item["base_version"] == "5.3.18"
        else:
            assert item["base_version"] == item["version"]


@pytest.mark.parallel
def test_package_get_base_version_matcher(bindings_cfg, catalog_repo, catalog_groups):
    """PackageGet uses base_version so 5.3.18 also hits 5.3.18.rhlw-00003, not 5.3.180."""
    path = _content_package_path(catalog_repo.pulp_href)
    data = _api_get(
        bindings_cfg,
        path,
        group_id=catalog_groups.example,
        artifact_id="hello",
        base_version="5.3.18",
        limit=100,
    )
    assert data["count"] == 2
    assert {item["version"] for item in data["results"]} == {"5.3.18", "5.3.18.rhlw-00003"}
    assert "collapse_builds" not in data["results"][0]

    neighbor = _api_get(
        bindings_cfg,
        path,
        group_id=catalog_groups.example,
        artifact_id="hello",
        base_version="5.3.180",
        limit=100,
    )
    assert neighbor["count"] == 1
    assert neighbor["results"][0]["version"] == "5.3.180"

    exact = _api_get(
        bindings_cfg,
        path,
        group_id=catalog_groups.example,
        artifact_id="hello",
        version="5.3.18",
        limit=100,
    )
    assert exact["count"] == 1
    assert exact["results"][0]["version"] == "5.3.18"

    startswith_dot = _api_get(
        bindings_cfg,
        path,
        group_id=catalog_groups.example,
        artifact_id="hello",
        version__startswith="5.3.18.",
        limit=100,
    )
    assert startswith_dot["count"] == 1
    assert startswith_dot["results"][0]["version"] == "5.3.18.rhlw-00003"

    # Bare startswith without the trailing dot is a false positive on 5.3.180.
    startswith_bare = _api_get(
        bindings_cfg,
        path,
        group_id=catalog_groups.example,
        artifact_id="hello",
        version__startswith="5.3.18",
        limit=100,
    )
    assert {item["version"] for item in startswith_bare["results"]} == {
        "5.3.18",
        "5.3.18.rhlw-00003",
        "5.3.180",
    }


@pytest.mark.parallel
def test_package_list_ordering_group_id_artifact_id(bindings_cfg, catalog_repo, catalog_groups):
    """Default order is group_id, artifact_id; -group_id reverses both fields."""
    default = _api_get(bindings_cfg, f"{catalog_repo.pulp_href}packages/")["results"]
    gas = [(pkg["group_id"], pkg["artifact_id"]) for pkg in default]
    assert gas == [
        (catalog_groups.example, "hello"),
        (catalog_groups.example, "world"),
        (catalog_groups.other, "widget"),
    ]

    explicit = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", ordering="group_id,artifact_id"
    )["results"]
    assert [(pkg["group_id"], pkg["artifact_id"]) for pkg in explicit] == gas

    implied = _api_get(bindings_cfg, f"{catalog_repo.pulp_href}packages/", ordering="group_id")[
        "results"
    ]
    assert [(pkg["group_id"], pkg["artifact_id"]) for pkg in implied] == gas

    reversed_rows = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", ordering="-group_id"
    )["results"]
    assert [(pkg["group_id"], pkg["artifact_id"]) for pkg in reversed_rows] == [
        (catalog_groups.other, "widget"),
        (catalog_groups.example, "world"),
        (catalog_groups.example, "hello"),
    ]

    page1 = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", ordering="-group_id", limit=1
    )
    page2 = _api_get(
        bindings_cfg,
        f"{catalog_repo.pulp_href}packages/",
        ordering="-group_id",
        limit=1,
        offset=1,
    )
    assert page1["count"] == page2["count"] == 3
    assert (page1["results"][0]["group_id"], page1["results"][0]["artifact_id"]) == (
        catalog_groups.other,
        "widget",
    )
    assert (page2["results"][0]["group_id"], page2["results"][0]["artifact_id"]) == (
        catalog_groups.example,
        "world",
    )


@pytest.mark.parallel
def test_package_list_ordering_last_updated(
    bindings_cfg,
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
):
    """last_updated is newest membership of any rebuild and is a sort key."""
    uid = _uid()
    later_group = f"org.zzz.{uid}"
    earlier_group = f"com.aaa.{uid}"
    repo = _upload_gavs(
        maven_artifact_api_client,
        maven_repo_api_client,
        maven_repo_factory,
        pom_file_factory,
        monitor_task,
        [(later_group, "later-name", "1.0.0")],
    )
    first = _api_get(bindings_cfg, f"{repo.pulp_href}packages/")["results"][0]
    first_updated = _parse_dt(first["last_updated"])
    assert first["last_updated"] == first["latest_releases"][0]["created_at"]

    repo = _add_gavs(
        maven_artifact_api_client,
        maven_repo_api_client,
        pom_file_factory,
        monitor_task,
        repo,
        [(earlier_group, "earlier-name", "1.0.0")],
    )
    rows = _api_get(bindings_cfg, f"{repo.pulp_href}packages/")["results"]
    by_ga = {(pkg["group_id"], pkg["artifact_id"]): pkg for pkg in rows}
    older = by_ga[(later_group, "later-name")]
    newer = by_ga[(earlier_group, "earlier-name")]
    assert _parse_dt(older["last_updated"]) == first_updated
    assert _parse_dt(newer["last_updated"]) > first_updated

    default_gas = [(pkg["group_id"], pkg["artifact_id"]) for pkg in rows]
    assert default_gas == [(earlier_group, "earlier-name"), (later_group, "later-name")]

    oldest_first = _api_get(
        bindings_cfg, f"{repo.pulp_href}packages/", ordering="last_updated", limit=100
    )["results"]
    assert [(pkg["group_id"], pkg["artifact_id"]) for pkg in oldest_first] == [
        (later_group, "later-name"),
        (earlier_group, "earlier-name"),
    ]

    by_updated = _api_get(
        bindings_cfg, f"{repo.pulp_href}packages/", ordering="-last_updated", limit=100
    )["results"]
    assert [(pkg["group_id"], pkg["artifact_id"]) for pkg in by_updated] == [
        (earlier_group, "earlier-name"),
        (later_group, "later-name"),
    ]

    repo = _add_gavs(
        maven_artifact_api_client,
        maven_repo_api_client,
        pom_file_factory,
        monitor_task,
        repo,
        [(later_group, "later-name", "1.0.0.rhlw-00003")],
    )
    after_rebuild = _api_get(
        bindings_cfg, f"{repo.pulp_href}packages/", ordering="-last_updated", limit=100
    )["results"]
    assert [(pkg["group_id"], pkg["artifact_id"]) for pkg in after_rebuild] == [
        (later_group, "later-name"),
        (earlier_group, "earlier-name"),
    ]
    zzz = after_rebuild[0]
    assert _parse_dt(zzz["last_updated"]) > _parse_dt(newer["last_updated"])
    rebuild_rel = next(rel for rel in zzz["latest_releases"] if rel["release"] == "rhlw-00003")
    assert zzz["last_updated"] == rebuild_rel["created_at"]


@pytest.mark.parallel
def test_package_list_ordering_invalid(bindings_cfg, catalog_repo):
    url = urljoin(bindings_cfg.host + "/", f"{catalog_repo.pulp_href}packages/".lstrip("/"))
    response = requests.get(
        url,
        params={"ordering": "name"},
        auth=(bindings_cfg.username, bindings_cfg.password),
    )
    assert response.status_code == 400, response.text
