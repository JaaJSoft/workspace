"""Reading a repository file must name its encoding.

``Path.read_text()`` and ``Path.write_text()`` fall back to the platform's
preferred encoding when none is given. That is UTF-8 in CI and cp1252 on a
Windows checkout, so a file holding one non-ASCII byte reads fine on the
machine that runs the matrix and raises ``UnicodeDecodeError`` on the machine
someone is working on. Nothing else in the suite can catch that: CI is exactly
the environment where the bug does not appear.

The scan is written against source text rather than against the tree alone, so
its own ability to fail is pinned by the synthetic cases below rather than by
whatever happens to be committed.
"""

import ast
import unittest
from pathlib import Path

from django.conf import settings

ROOTS = ("workspace", "scripts")
WATCHED = ("read_text", "write_text")


def _left_to_the_platform(call):
    """Whether *call* lets the platform pick the encoding.

    Saying ``encoding=None`` is the default spelled out rather than an
    encoding named, so it reads cp1252 on Windows exactly like the bare
    call does. Any other value is a decision somebody took on purpose -
    the rule is that the choice is written down, not that it is UTF-8.
    """
    for keyword in call.keywords:
        if keyword.arg == "encoding":
            value = keyword.value
            return isinstance(value, ast.Constant) and value.value is None
    return True


def offenders(source, label="<source>"):
    """Every ``read_text``/``write_text`` call in *source* naming no encoding."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in WATCHED
            and _left_to_the_platform(node)
        ):
            found.append(f"{label}:{node.lineno} {node.func.attr}()")
    return found


class TextFileEncodingTests(unittest.TestCase):
    def test_no_call_in_the_tree_leaves_its_encoding_to_the_platform(self):
        base = Path(settings.BASE_DIR)
        found = []
        for root in ROOTS:
            for path in sorted((base / root).rglob("*.py")):
                relative = path.relative_to(base).as_posix()
                found += offenders(path.read_text(encoding="utf-8"), relative)
        self.assertEqual(
            found,
            [],
            'pass encoding="utf-8": without it these read as cp1252 on Windows',
        )

    def test_the_scan_sees_a_call_that_names_no_encoding(self):
        self.assertEqual(
            offenders("Path('x').read_text()", "x.py"), ["x.py:1 read_text()"]
        )
        self.assertEqual(
            offenders("Path('x').write_text(body)", "x.py"), ["x.py:1 write_text()"]
        )

    def test_the_scan_sees_an_encoding_handed_back_to_the_platform(self):
        self.assertEqual(
            offenders("Path('x').read_text(encoding=None)", "x.py"),
            ["x.py:1 read_text()"],
        )

    def test_the_scan_leaves_a_call_that_names_one_alone(self):
        self.assertEqual(offenders('Path("x").read_text(encoding="utf-8")'), [])
        self.assertEqual(
            offenders('Path("x").write_text(body, encoding="latin-1")'), []
        )

    def test_the_scan_ignores_a_method_of_the_same_name_on_something_else(self):
        """Only the keyword matters, not the receiver: a helper of one's own
        called ``read_text`` would be flagged too, and that is the intent -
        the rule is about the argument, not about pathlib."""
        self.assertEqual(offenders("response.json()"), [])
        self.assertEqual(offenders("widget.read_text(encoding=enc)"), [])
