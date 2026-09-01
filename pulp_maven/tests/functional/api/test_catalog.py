"""Catalog API tests.

Generated client methods are unavailable until `oci-env generate-client` is rerun.
"""

from urllib.parse import urljoin

import pytest
import requests


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
    assert set(pkg["versions"]) == {rel["version"] for rel in pkg["latest_releases"]}
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


@pytest.fixture
def catalog_repo(
    maven_artifact_api_client,
    maven_repo_api_client,
    maven_repo_factory,
    pom_file_factory,
    monitor_task,
):
    """Three GAs; hello has two versions plus a Lightwell rebuild and a 5.3.180 neighbor."""
    # Upload order sets pulp_created: unsuffixed 5.3.18 first, then the rebuild.
    gavs = [
        ("com.example", "hello", "5.3.18"),
        ("com.example", "hello", "5.3.18.rhlw-00003"),
        ("com.example", "hello", "5.3.180"),
        ("com.example", "hello", "1.0.0"),
        ("com.example", "world", "1.0.0"),
        ("org.other", "widget", "1.0.0"),
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
def test_package_list_grouping_and_pagination(bindings_cfg, catalog_repo):
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
        ("com.example", "hello"),
        ("com.example", "world"),
        ("org.other", "widget"),
    }
    hello = next(pkg for pkg in all_rows if pkg["artifact_id"] == "hello")
    _assert_package_row(hello)
    assert set(hello["versions"]) == {"1.0.0", "5.3.18", "5.3.180"}
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
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", group_id__istartswith="com.example"
    )
    assert data["count"] == 2
    assert {pkg["artifact_id"] for pkg in data["results"]} == {"hello", "world"}

    data = _api_get(
        bindings_cfg, f"{catalog_repo.pulp_href}packages/", group_id__istartswith="COM.EXAMPLE"
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
def test_collapse_builds_and_base_version(bindings_cfg, catalog_repo):
    """collapse_builds keeps one unit per logical version; base_version is always present."""
    path = _content_package_path(catalog_repo.pulp_href)
    repo_version = catalog_repo.latest_version_href

    expanded = _api_get(
        bindings_cfg,
        path,
        group_id="com.example",
        artifact_id="hello",
        repository_version=repo_version,
        collapse_builds="false",
        limit=100,
    )
    default = _api_get(
        bindings_cfg,
        path,
        group_id="com.example",
        artifact_id="hello",
        repository_version=repo_version,
        limit=100,
    )
    collapsed = _api_get(
        bindings_cfg,
        path,
        group_id="com.example",
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
def test_package_get_base_version_matcher(bindings_cfg, catalog_repo):
    """PackageGet uses base_version so 5.3.18 also hits 5.3.18.rhlw-00003, not 5.3.180."""
    path = _content_package_path(catalog_repo.pulp_href)
    data = _api_get(
        bindings_cfg,
        path,
        group_id="com.example",
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
        group_id="com.example",
        artifact_id="hello",
        base_version="5.3.180",
        limit=100,
    )
    assert neighbor["count"] == 1
    assert neighbor["results"][0]["version"] == "5.3.180"

    exact = _api_get(
        bindings_cfg,
        path,
        group_id="com.example",
        artifact_id="hello",
        version="5.3.18",
        limit=100,
    )
    assert exact["count"] == 1
    assert exact["results"][0]["version"] == "5.3.18"

    startswith_dot = _api_get(
        bindings_cfg,
        path,
        group_id="com.example",
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
        group_id="com.example",
        artifact_id="hello",
        version__startswith="5.3.18",
        limit=100,
    )
    assert {item["version"] for item in startswith_bare["results"]} == {
        "5.3.18",
        "5.3.18.rhlw-00003",
        "5.3.180",
    }
