import pytest

from pulp_maven.app.versions import rebuild_release, strip_build_suffix


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
