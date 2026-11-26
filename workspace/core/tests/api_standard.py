"""Finds where the API departs from its standard (CLAUDE.md, *API standard*).

``find_gaps()`` answers, per rule, the paths, files or views that break it.
``test_api_standard`` compares that with the gaps already known.
"""

import re
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from drf_spectacular.drainage import GeneratorStats
from drf_spectacular.generators import SchemaGenerator

WORKSPACE = Path(settings.BASE_DIR) / "workspace"
PREFIX = "/api/v1/"
METHODS = ("get", "post", "put", "patch", "delete")

# First segments that are not a module: the resources the standard keeps at the
# root, and what spans modules.
ROOT_SEGMENTS = {
    "events",
    "calendars",
    "groups",
    "search",
    "settings",
    "activity",
    "modules",
    "stream",
    "auth",
}
MODULE_SEGMENTS = {
    "ai",
    "calendar",
    "chat",
    "files",
    "imports",
    "mail",
    "notifications",
    "people",
    "photos",
    "projects",
    "users",
    "vault",
}
# Parameters that are not the identifier of a resource.
PLAIN_PARAMETERS = {"token", "module", "key"}
BANNED_SEGMENTS = {
    "delete",
    "remove",
    "clear",
    "list",
    "bulk",
    "bulk-delete",
    "bulk-download",
    "batch-action",
    "actions",
    "pin-reorder",
}
BANNED_QUERY_NAMES = {"search", "page", "page_size", "filter"}
BANNED_QUERY_AFFIXES = re.compile(r"^show_|_ids?$|_uuid$|_from$|_to$")

HAND_READ_QUERY = re.compile(r"query_params\s*(?:\.get(?:list)?\(|\[)")
BODILESS_ERROR = re.compile(r"Response\(\s*status=status\.HTTP_4\d\d")
ERROR_KEY = re.compile(r"""\{\s*["']error["']\s*:""")
OTHER_PAGINATOR = re.compile(
    r"\b(?:OptInLimitOffsetPagination|PageNumberPagination|LimitOffsetPagination"
    r"|CursorPagination)\b"
)


def _path_gaps(path, operations, gaps):
    segments = path.removeprefix(PREFIX).split("/")
    parameters = [s[1:-1] for s in segments if s.startswith("{")]
    identifiers = [p for p in parameters if p not in PLAIN_PARAMETERS]
    ends_on_identifier = segments[-1].startswith("{") and bool(identifiers)

    if segments[0] not in ROOT_SEGMENTS | MODULE_SEGMENTS:
        gaps["namespace"].add(path)

    for position, name in enumerate(identifiers):
        last = position == len(identifiers) - 1
        named_as_id = name.endswith("_id") or name in ("id", "pk")
        if (
            (named_as_id and name != "user_id")
            or (last and ends_on_identifier and name not in ("uuid", "user_id"))
            or (not last and name == "uuid")
        ):
            gaps["path parameter"].add(path)
            break

    addressed_through_a_parent = (
        len(identifiers) == 2 and ends_on_identifier and identifiers[-1] != "user_id"
    )
    if len(identifiers) >= 3 or addressed_through_a_parent:
        gaps["nesting"].add(path)

    for position, segment in enumerate(segments):
        if (
            segment in BANNED_SEGMENTS
            or (segment == "search" and len(segments) > 1)
            or (segment == "reorder" and segments[position - 1].startswith("{"))
        ):
            gaps["path vocabulary"].add(path)

    if "put" in operations and "patch" not in operations and ends_on_identifier:
        gaps["update without PATCH"].add(path)

    for method in METHODS:
        for parameter in operations.get(method, {}).get("parameters", []):
            name = parameter["name"]
            if parameter["in"] == "query" and (
                name in BANNED_QUERY_NAMES or BANNED_QUERY_AFFIXES.search(name)
            ):
                gaps["query parameter"].add(f"{path} ?{name}")


def _view_sources():
    for pattern in ("*/views.py", "*/views/*.py", "*/viewsets/*.py"):
        yield from WORKSPACE.glob(pattern)


def _source_gaps(gaps):
    for source in _view_sources():
        text = source.read_text(encoding="utf-8")
        name = source.relative_to(WORKSPACE).as_posix()
        if HAND_READ_QUERY.search(text):
            gaps["query parameter read by hand"].add(name)
        if BODILESS_ERROR.search(text):
            gaps["error without a body"].add(name)
        if ERROR_KEY.search(text):
            gaps["error under another key"].add(name)
    for source in WORKSPACE.rglob("*.py"):
        name = source.relative_to(WORKSPACE).as_posix()
        if "/tests/" in name or name == "common/pagination.py":
            continue
        if OTHER_PAGINATOR.search(source.read_text(encoding="utf-8")):
            gaps["another paginator"].add(name)


def _complaint_subject(message):
    """What a schema generation message is about: a view, or the schema itself."""
    view = re.search(r"(?:Error|Warning) \[(\w+)", message)
    if view:
        return view.group(1)
    operation = re.search(r'operationId "(\w+)"', message)
    if operation:
        return f"operationId {operation.group(1)}"
    enum = re.search(r'fields named "(\w+)"', message)
    if enum:
        return f"enum {enum.group(1)}"
    return message


def _generate_schema(gaps):
    def record(stats, message, severity):
        _, breadcrumbs = stats._get_current_trace()
        prefix = f"{severity.capitalize()} [{breadcrumbs}]: " if breadcrumbs else ""
        gaps["schema complaint"].add(_complaint_subject(f"{prefix}{message}"))

    with patch.object(GeneratorStats, "emit", record):
        return SchemaGenerator().get_schema(request=None, public=True)


def find_gaps():
    gaps = defaultdict(set)
    schema = _generate_schema(gaps)
    for path, operations in schema["paths"].items():
        if path.startswith(PREFIX):
            _path_gaps(path, operations, gaps)
    _source_gaps(gaps)
    return gaps
