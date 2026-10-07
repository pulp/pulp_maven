import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urljoin

SIZE_UNITS = {
    "kB": 1_000,
    "MB": 1_000_000,
    "GB": 1_000_000_000,
    "TB": 1_000_000_000_000,
    "PB": 1_000_000_000_000_000,
    "EB": 1_000_000_000_000_000_000,
    "ZB": 1_000_000_000_000_000_000_000,
    "YB": 1_000_000_000_000_000_000_000_000,
}

SIZE_RE = re.compile(r"^(?P<value>\d+(?:\.\d+)?) (?P<unit>\w+)$")


def parse_size(value):
    """Return a representative byte count for a Jinja-formatted file size."""
    if not value:
        return None

    match = SIZE_RE.fullmatch(value)
    if not match:
        return None

    unit = match["unit"]
    if unit in {"Byte", "Bytes"}:
        return int(Decimal(match["value"]))

    multiplier = SIZE_UNITS.get(unit)
    if multiplier is None:
        return None
    size = int(Decimal(match["value"]) * multiplier)

    # Keep rounded values such as "1000.0 kB" below the MB boundary.
    units = list(SIZE_UNITS)
    position = units.index(unit)
    if position + 1 < len(units):
        next_boundary = SIZE_UNITS[units[position + 1]]
        size = min(size, next_boundary - 1)

    return size


DATE_RE = re.compile(r"\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}")


@dataclass(frozen=True)
class ListingEntry:
    name: str
    href: str
    is_directory: bool
    modified: datetime | None
    size_text: str | None
    size: int | None

    def absolute_url(self, listing_url: str) -> str:
        return urljoin(listing_url, self.href)


class PulpListingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries: list[ListingEntry] = []

        self.in_pre = False
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.in_pre = True

        elif tag == "a" and self.in_pre:
            self._finish_anchor()

            attributes = dict(attrs)
            self.anchor = {
                "href": attributes.get("href"),
                "name": [],
                "trailing_text": [],
                "closed": False,
            }

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            self.anchor["closed"] = True
        elif tag == "pre":
            self._finish_anchor()
            self.in_pre = False

    def handle_data(self, data):
        if not self.anchor:
            return

        if self.anchor["closed"]:
            self.anchor["trailing_text"].append(data)
        else:
            self.anchor["name"].append(data)

    def close(self):
        super().close()
        self._finish_anchor()

    def _finish_anchor(self):
        if not self.anchor:
            return

        anchor = self.anchor
        self.anchor = None

        href = anchor["href"]
        name = "".join(anchor["name"]).strip()

        # Ignore parent-directory links and unrelated links.
        if not href or href == "../" or not href.startswith("./"):
            return

        trailing = "".join(anchor["trailing_text"])
        trailing = trailing.split("\n", 1)[0].strip()

        date_match = DATE_RE.search(trailing)
        modified = None
        size_text = trailing or None

        if date_match:
            modified = datetime.strptime(
                date_match.group(),
                "%d-%b-%Y %H:%M",
            )
            size_text = trailing[date_match.end() :].strip() or None

        self.entries.append(
            ListingEntry(
                name=name,
                href=href,
                is_directory=href.endswith("/") or name.endswith("/"),
                modified=modified,
                size_text=size_text,
                size=parse_size(size_text),
            )
        )
