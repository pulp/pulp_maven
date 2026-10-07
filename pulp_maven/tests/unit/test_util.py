from datetime import datetime

import pytest
from jinja2.filters import do_filesizeformat

from pulp_maven.app.util import PulpListingParser, parse_size


@pytest.mark.parametrize(
    ("display_size", "expected"),
    [
        (None, None),
        ("", None),
        ("1 Byte", 1),
        ("64 Bytes", 64),
        ("1.2 kB", 1_200),
        ("1000.0 kB", 999_999),
        ("1.2 MB", 1_200_000),
        ("unknown", None),
    ],
)
def test_parse_size(display_size, expected):
    assert parse_size(display_size) == expected


@pytest.mark.parametrize(
    "display_size",
    ["0 Bytes", "1 Byte", "64 Bytes", "1.2 kB", "1000.0 kB", "1.2 MB"],
)
def test_parsed_size_renders_identically(display_size):
    assert do_filesizeformat(parse_size(display_size)) == display_size


def test_listing_parser_extracts_entries():
    parser = PulpListingParser()
    parser.feed(
        """
        <html><body><pre>
        <a href="../">../</a>
        <a href="./child/">child/</a> 06-Oct-2026 12:34
        <a href="./library.jar">library.jar</a> 06-Oct-2026 12:35  1.2 kB
        </pre></body></html>
        """
    )
    parser.close()

    assert [entry.name for entry in parser.entries] == ["child/", "library.jar"]
    assert parser.entries[0].is_directory
    assert parser.entries[0].modified == datetime(2026, 10, 6, 12, 34)
    assert parser.entries[0].size is None
    assert not parser.entries[1].is_directory
    assert parser.entries[1].modified == datetime(2026, 10, 6, 12, 35)
    assert parser.entries[1].size == 1_200
