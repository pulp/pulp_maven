import pytest

from pulp_maven.app.versions import (
    normalize_package_index_ordering,
    parse_package_search,
    rebuild_release,
    strip_build_suffix,
    version_sort_key,
)


@pytest.mark.parametrize(
    "version,expected",
    [
        ("5.3.18", "5.3.18"),
        ("5.3.18.rhlw-00003", "5.3.18"),
        ("5.3.18.lw-1", "5.3.18"),
        ("0.1.rhlw-00003", "0.1"),
        ("5.3.18.anything", "5.3.18.anything"),
        ("5.3.18-anything", "5.3.18-anything"),
        ("4.3.0-redhat-1", "4.3.0-redhat-1"),
        ("5.3.180", "5.3.180"),
        ("1.0.rhlw-00003.extra", "1.0.rhlw-00003.extra"),
        ("1.0.0.abc-1", "1.0.0"),
        ("1.0.0.ABC-99", "1.0.0"),
        ("1.0.foo-bar", "1.0.foo-bar"),
        ("", ""),
        (None, None),
    ],
)
def test_strip_build_suffix(version, expected):
    assert strip_build_suffix(version) == expected


@pytest.mark.parametrize(
    "version,expected",
    [
        ("5.3.18", ""),
        ("5.3.18.rhlw-00003", "rhlw-00003"),
        ("5.3.18.lw-1", "lw-1"),
        ("0.1.rhlw-00003", "rhlw-00003"),
        ("5.3.18.anything", ""),
        ("5.3.18-anything", ""),
        ("4.3.0-redhat-1", ""),
        ("5.3.180", ""),
        ("1.0.rhlw-00003.extra", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_rebuild_release(version, expected):
    assert rebuild_release(version) == expected


@pytest.mark.parametrize(
    "versions,expected",
    [
        # Lexical sort would put 1.10 before 1.2 and 1.9.
        (["1.10", "1.9", "1.2"], ["1.2", "1.9", "1.10"]),
        # Lexical sort would put 5.3.18 before 5.3.9 ('1' < '9').
        (["5.3.18", "5.3.9", "5.3.180"], ["5.3.9", "5.3.18", "5.3.180"]),
        (["5.3.180", "1.0.0", "5.3.18"], ["1.0.0", "5.3.18", "5.3.180"]),
        ([], []),
        ([""], [""]),
    ],
)
def test_version_sort_key_orders_numeric_tokens(versions, expected):
    assert sorted(versions, key=version_sort_key) == expected


def test_version_sort_key_newest_first():
    """Catalog versions / latest_releases use this key with reverse=True."""
    assert sorted(["1.10", "1.9", "1.2"], key=version_sort_key, reverse=True) == [
        "1.10",
        "1.9",
        "1.2",
    ]


def test_version_sort_key_empty_and_none():
    assert version_sort_key("") == ()
    assert version_sort_key(None) == ()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ([], ["group_id", "artifact_id"]),
        ([""], ["group_id", "artifact_id"]),
        (["group_id"], ["group_id", "artifact_id"]),
        (["group_id,artifact_id"], ["group_id", "artifact_id"]),
        (["-group_id"], ["-group_id", "-artifact_id"]),
        (["last_updated"], ["last_updated", "group_id", "artifact_id"]),
        (["-last_updated"], ["-last_updated", "group_id", "artifact_id"]),
        (["-last_updated", "artifact_id"], ["-last_updated", "artifact_id", "group_id"]),
        (["artifact_id"], ["artifact_id", "group_id"]),
    ],
)
def test_normalize_package_index_ordering(raw, expected):
    assert normalize_package_index_ordering(raw) == expected


def test_normalize_package_index_ordering_rejects_unknown():
    with pytest.raises(ValueError, match="Unknown ordering field"):
        normalize_package_index_ordering(["name"])


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        (":", None),
        (" : ", None),
        ("::", None),
        ("oo", ("or", "oo")),
        ("  ba  ", ("or", "ba")),
        ("junit", ("or", "junit")),
        ("foo:junit", ("and", "foo", "junit")),
        ("foo : junit", ("and", "foo", "junit")),
        ("FOO:JUNIT", ("and", "FOO", "JUNIT")),
        ("foo:", ("and", "foo", "")),
        (":junit", ("and", "", "junit")),
        (" :junit", ("and", "", "junit")),
        ("foo.bar:bar-junit:1.2.3", ("and", "foo.bar", "bar-junit")),
        ("foo::1.0", ("and", "foo", "")),
    ],
)
def test_parse_package_search(raw, expected):
    assert parse_package_search(raw) == expected
