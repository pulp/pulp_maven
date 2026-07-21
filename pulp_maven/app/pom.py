"""POM XML parsing utilities for MavenPackage metadata extraction."""

import re

import defusedxml.ElementTree as ET

MAVEN_NS = "http://maven.apache.org/POM/4.0.0"
PROP_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _find(element, tag):
    """Find a child element, trying the Maven namespace first, then bare."""
    el = element.find(f"{{{MAVEN_NS}}}{tag}")
    if el is None:
        el = element.find(tag)
    return el


def _findall(element, tag):
    """Find all child elements, trying the Maven namespace first, then bare."""
    result = element.findall(f"{{{MAVEN_NS}}}{tag}")
    if not result:
        result = element.findall(tag)
    return result


def _findtext(element, tag, default=""):
    """Get text of a child element, trying the Maven namespace first."""
    text = element.findtext(f"{{{MAVEN_NS}}}{tag}")
    if text is None:
        text = element.findtext(tag)
    return text if text is not None else default


def _collect_properties(root):
    """Collect explicit <properties> and implicit project.* properties."""
    props = {}

    props_el = _find(root, "properties")
    if props_el is not None:
        for child in props_el:
            key = child.tag.replace(f"{{{MAVEN_NS}}}", "")
            if child.text:
                props[key] = child.text.strip()

    implicit = {
        "project.groupId": _findtext(root, "groupId"),
        "project.artifactId": _findtext(root, "artifactId"),
        "project.version": _findtext(root, "version"),
        "project.packaging": _findtext(root, "packaging", "jar"),
        "project.name": _findtext(root, "name"),
        "project.description": _findtext(root, "description"),
        "project.url": _findtext(root, "url"),
    }
    for key, value in implicit.items():
        if value:
            props[key] = value

    return props


def _resolve(value, props):
    """Resolve ${...} placeholders in a string using the properties dict."""
    if not value or "${" not in value:
        return value

    for _ in range(10):
        resolved = PROP_PATTERN.sub(lambda m: props.get(m.group(1), m.group(0)), value)
        if resolved == value:
            break
        value = resolved

    return value


def parse_pom_metadata(file_obj):
    """Parse a POM file and return metadata fields for MavenPackage.

    Args:
        file_obj: A file-like object containing POM XML.

    Returns:
        A dict with keys: name, description, packaging, url, licenses,
        dependencies, scm_url.  Returns None on parse failure.
    """
    try:
        tree = ET.parse(file_obj)
        root = tree.getroot()
    except (ET.ParseError, Exception):
        return None

    props = _collect_properties(root)

    result = {
        "name": _resolve(_findtext(root, "name"), props) or None,
        "description": _resolve(_findtext(root, "description"), props) or None,
        "packaging": _resolve(_findtext(root, "packaging", "jar"), props) or None,
        "url": _resolve(_findtext(root, "url"), props) or None,
        "licenses": None,
        "dependencies": None,
        "scm_url": None,
    }

    lics_el = _find(root, "licenses")
    if lics_el is not None:
        result["licenses"] = []
        for lic in _findall(lics_el, "license"):
            result["licenses"].append(
                {
                    "name": _resolve(_findtext(lic, "name"), props),
                    "url": _resolve(_findtext(lic, "url"), props),
                }
            )

    deps_el = _find(root, "dependencies")
    if deps_el is not None:
        result["dependencies"] = []
        for dep in _findall(deps_el, "dependency"):
            result["dependencies"].append(
                {
                    "group_id": _resolve(_findtext(dep, "groupId"), props),
                    "artifact_id": _resolve(_findtext(dep, "artifactId"), props),
                    "version": _resolve(_findtext(dep, "version"), props),
                    "scope": _resolve(_findtext(dep, "scope"), props) or None,
                    "optional": _resolve(_findtext(dep, "optional"), props) == "true",
                }
            )

    scm_el = _find(root, "scm")
    if scm_el is not None:
        result["scm_url"] = _resolve(_findtext(scm_el, "url"), props) or None

    return result
