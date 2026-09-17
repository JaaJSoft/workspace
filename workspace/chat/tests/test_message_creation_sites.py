"""Every chat message is created by ``post_message``, nowhere else.

A posted message owes its conversation a set of side effects, and the helper
in ``chat/services/posting.py`` is the one place that wires them. A site that
builds the row itself has to remember to deliver it, and forgetting is silent:
the author's own client renders the message either way. This test makes the
rule mechanical - application code that reaches for ``Message.objects.create``
(or the constructor) fails here, at the offending line.

Test code and migrations are exempt: a fixture is building state, and a
migration is a historical record that must not import live services.
"""

import ast
from pathlib import Path

from django.test import SimpleTestCase

import workspace

PROJECT_DIR = Path(workspace.__file__).parent
CREATION_SITE = PROJECT_DIR / "chat" / "services" / "posting.py"

_MANAGER_METHODS = {"create", "bulk_create", "get_or_create", "update_or_create"}


def _application_sources():
    for path in sorted(PROJECT_DIR.rglob("*.py")):
        parts = path.relative_to(PROJECT_DIR).parts
        if "tests" in parts or "migrations" in parts:
            continue
        if path == CREATION_SITE:
            continue
        yield path


def _is_message(node):
    return isinstance(node, ast.Name) and node.id == "Message"


def _creates_a_message(call):
    """``Message(...)`` or ``Message.objects.<create-like>(...)``."""
    func = call.func
    if _is_message(func):
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr in _MANAGER_METHODS
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "objects"
        and _is_message(func.value.value)
    )


def _creation_sites(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _creates_a_message(node)
    ]


class MessageCreationSitesTests(SimpleTestCase):
    def test_only_post_message_creates_message_rows(self):
        offences = [
            f"{path.relative_to(PROJECT_DIR.parent)}:{line}"
            for path in _application_sources()
            for line in _creation_sites(path)
        ]
        self.assertEqual(
            offences,
            [],
            "Message rows are created by chat.services.posting.post_message, "
            "never at the call site - it is the one path that delivers the "
            "message. Offending sites:\n  " + "\n  ".join(offences),
        )
